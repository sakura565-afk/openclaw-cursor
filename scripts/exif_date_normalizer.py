#!/usr/bin/env python3
"""Normalize photo filenames using EXIF DateTimeOriginal metadata."""

from __future__ import annotations

import argparse
import csv
import re
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Iterable, Optional

import piexif
from PIL import Image
from tqdm import tqdm
from zoneinfo import ZoneInfo


SUPPORTED_SUFFIXES = {".jpg", ".jpeg", ".png", ".tif", ".tiff", ".heic"}
FOLDER_DATE_PATTERN = re.compile(r"(20\d{2})[-_]?([01]\d)[-_]?([0-3]\d)")


@dataclass
class RenameDecision:
    old_name: str
    new_name: str
    date_source: str
    status: str


def parse_args(argv: Optional[list[str]] = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Normalize photo filenames by EXIF DateTimeOriginal."
    )
    parser.add_argument("--scan", required=True, help="Path to recursively scan")
    parser.add_argument(
        "--fix",
        action="store_true",
        help="Apply file renaming (without --fix runs in preview mode)",
    )
    parser.add_argument(
        "--folder-date",
        action="store_true",
        help="Fallback to a folder date when EXIF DateTimeOriginal is missing",
    )
    parser.add_argument(
        "--tz",
        default="Europe/Moscow",
        help="IANA timezone name used for normalized timestamp",
    )
    parser.add_argument(
        "--csv-log",
        default="exif_date_normalizer_log.csv",
        help="CSV log output path",
    )
    return parser.parse_args(argv)


def iter_supported_files(root: Path) -> Iterable[Path]:
    for path in root.rglob("*"):
        if path.is_file() and path.suffix.lower() in SUPPORTED_SUFFIXES:
            yield path


def parse_exif_datetime(raw_value: str, tz_name: str) -> Optional[datetime]:
    try:
        parsed = datetime.strptime(raw_value, "%Y:%m:%d %H:%M:%S")
        return parsed.replace(tzinfo=ZoneInfo(tz_name))
    except (ValueError, TypeError):
        return None


def read_exif_datetime(path: Path, tz_name: str) -> Optional[datetime]:
    exif_raw: Optional[str] = None

    if path.suffix.lower() in {".jpg", ".jpeg", ".tif", ".tiff"}:
        try:
            exif_data = piexif.load(str(path))
            exif_value = exif_data.get("Exif", {}).get(piexif.ExifIFD.DateTimeOriginal)
            if isinstance(exif_value, bytes):
                exif_raw = exif_value.decode("utf-8", errors="ignore")
            elif isinstance(exif_value, str):
                exif_raw = exif_value
        except (piexif.InvalidImageDataError, OSError, ValueError):
            exif_raw = None

    if exif_raw is None:
        try:
            with Image.open(path) as image:
                exif = image.getexif()
                exif_value = exif.get(36867) if exif else None
                if isinstance(exif_value, bytes):
                    exif_raw = exif_value.decode("utf-8", errors="ignore")
                elif isinstance(exif_value, str):
                    exif_raw = exif_value
        except OSError:
            return None

    if not exif_raw:
        return None

    return parse_exif_datetime(exif_raw.strip(), tz_name)


def read_folder_datetime(path: Path, tz_name: str) -> Optional[datetime]:
    for parent in path.parents:
        match = FOLDER_DATE_PATTERN.search(parent.name)
        if not match:
            continue
        year, month, day = map(int, match.groups())
        try:
            return datetime(year, month, day, 0, 0, 0, tzinfo=ZoneInfo(tz_name))
        except ValueError:
            continue
    return None


def build_new_name(path: Path, shot_at: datetime) -> str:
    normalized_date = shot_at.strftime("%Y-%m-%d_%H-%M-%S")
    original_stem = re.sub(r"\s+", "_", path.stem.strip())
    original_stem = original_stem or "image"
    return f"{normalized_date}_{original_stem}{path.suffix.lower()}"


def ensure_unique_target(path: Path, new_name: str) -> Path:
    candidate = path.with_name(new_name)
    if candidate == path:
        return candidate

    if not candidate.exists():
        return candidate

    stem = candidate.stem
    suffix = candidate.suffix
    index = 1
    while True:
        versioned = candidate.with_name(f"{stem}_{index}{suffix}")
        if not versioned.exists():
            return versioned
        index += 1


def process_file(
    path: Path, apply_fix: bool, use_folder_date: bool, tz_name: str
) -> RenameDecision:
    shot_at = read_exif_datetime(path, tz_name)
    source = "exif"
    if shot_at is None and use_folder_date:
        shot_at = read_folder_datetime(path, tz_name)
        source = "folder_date" if shot_at else source

    if shot_at is None:
        return RenameDecision(path.name, path.name, "missing", "skipped")

    new_name = build_new_name(path, shot_at)
    target_path = ensure_unique_target(path, new_name)
    if target_path.name != new_name:
        source = f"{source}+deduplicated"

    if target_path == path:
        return RenameDecision(path.name, path.name, source, "unchanged")

    if not apply_fix:
        return RenameDecision(path.name, target_path.name, source, "preview")

    path.rename(target_path)
    return RenameDecision(path.name, target_path.name, source, "renamed")


def write_csv_log(log_path: Path, decisions: list[RenameDecision]) -> None:
    log_path.parent.mkdir(parents=True, exist_ok=True)
    with log_path.open("w", newline="", encoding="utf-8") as csv_file:
        writer = csv.writer(csv_file)
        writer.writerow(["old_name", "new_name", "date_source", "status"])
        for item in decisions:
            writer.writerow([item.old_name, item.new_name, item.date_source, item.status])


def main(argv: Optional[list[str]] = None) -> int:
    args = parse_args(argv)
    root = Path(args.scan).expanduser().resolve()
    if not root.exists():
        raise SystemExit(f"Scan path does not exist: {root}")
    if not root.is_dir():
        raise SystemExit(f"Scan path is not a directory: {root}")

    try:
        ZoneInfo(args.tz)
    except Exception as exc:  # pragma: no cover - platform-specific zone db
        raise SystemExit(f"Invalid timezone '{args.tz}': {exc}") from exc

    files = list(iter_supported_files(root))
    decisions: list[RenameDecision] = []
    for file_path in tqdm(files, desc="Scanning files"):
        decisions.append(process_file(file_path, args.fix, args.folder_date, args.tz))

    write_csv_log(Path(args.csv_log), decisions)

    renamed = sum(1 for d in decisions if d.status == "renamed")
    preview = sum(1 for d in decisions if d.status == "preview")
    skipped = sum(1 for d in decisions if d.status == "skipped")
    unchanged = sum(1 for d in decisions if d.status == "unchanged")
    mode = "APPLY" if args.fix else "DRY-RUN"
    print(
        f"[{mode}] processed={len(decisions)} renamed={renamed} preview={preview} "
        f"unchanged={unchanged} skipped={skipped} log={args.csv_log}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
