#!/usr/bin/env python3
"""Photo archive deduplication with perceptual and average hashes."""

from __future__ import annotations

import argparse
import csv
import json
import os
import shutil
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

import imagehash
from PIL import Image, UnidentifiedImageError
from tqdm import tqdm


RAW_EXTENSIONS = {".cr2", ".nef", ".arw", ".dng", ".raf", ".orf"}
SUPPORTED_EXTENSIONS = {".jpg", ".jpeg", ".png", ".tiff", ".tif", ".bmp", ".heic", *RAW_EXTENSIONS}
DEFAULT_THRESHOLD = 95.0


try:  # pragma: no cover - optional runtime dependency
    from pillow_heif import register_heif_opener

    register_heif_opener()
except Exception:  # pragma: no cover - HEIC may be unsupported
    pass

try:  # pragma: no cover - optional runtime dependency
    import rawpy  # type: ignore
except Exception:  # pragma: no cover - RAW support may be unavailable
    rawpy = None


@dataclass(frozen=True)
class HashRecord:
    path: Path
    perceptual_hash: imagehash.ImageHash | None
    average_hash: imagehash.ImageHash | None
    file_size: int
    modified_time: float


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Find and process duplicate photos by perceptual similarity.",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    parser.add_argument("--scan", required=True, type=Path, help="Root directory to scan recursively.")
    parser.add_argument("--dry-run", action="store_true", help="Only report duplicates without deleting/moving.")
    parser.add_argument("--move", action="store_true", help="Move duplicates to <scan>/duplicates instead of deleting.")
    parser.add_argument(
        "--hash-type",
        default="both",
        choices=("perceptual", "average", "both"),
        help="Hashing mode for similarity comparison.",
    )
    parser.add_argument(
        "--threshold",
        type=float,
        default=DEFAULT_THRESHOLD,
        help="Duplicate similarity threshold in percent.",
    )
    parser.add_argument("--json-out", type=Path, default=Path("photo_deduplication_report.json"))
    parser.add_argument("--csv-out", type=Path, default=Path("photo_deduplication_duplicates.csv"))
    return parser.parse_args(argv)


def iter_image_paths(root: Path) -> list[Path]:
    return sorted(
        path
        for path in root.rglob("*")
        if path.is_file() and path.suffix.lower() in SUPPORTED_EXTENSIONS
    )


def hash_image(path: Path, hash_type: str) -> HashRecord | None:
    try:
        if path.suffix.lower() in RAW_EXTENSIONS:
            if rawpy is None:
                raise RuntimeError("rawpy is required for RAW formats")
            with rawpy.imread(str(path)) as raw:
                converted = Image.fromarray(raw.postprocess()).convert("RGB")
        else:
            with Image.open(path) as image:
                converted = image.convert("RGB")
            perceptual_hash = imagehash.phash(converted) if hash_type in ("perceptual", "both") else None
            average_hash = imagehash.average_hash(converted) if hash_type in ("average", "both") else None
    except (UnidentifiedImageError, OSError, ValueError, RuntimeError):
        return None

    stat = path.stat()
    return HashRecord(
        path=path,
        perceptual_hash=perceptual_hash,
        average_hash=average_hash,
        file_size=stat.st_size,
        modified_time=stat.st_mtime,
    )


def _single_similarity(
    left: imagehash.ImageHash | None,
    right: imagehash.ImageHash | None,
) -> float | None:
    if left is None or right is None:
        return None
    distance = left - right
    bits = left.hash.size
    return max(0.0, (1.0 - (distance / bits)) * 100.0)


def similarity(record_a: HashRecord, record_b: HashRecord, hash_type: str) -> float:
    scores: list[float] = []
    if hash_type in ("perceptual", "both"):
        p_score = _single_similarity(record_a.perceptual_hash, record_b.perceptual_hash)
        if p_score is not None:
            scores.append(p_score)
    if hash_type in ("average", "both"):
        a_score = _single_similarity(record_a.average_hash, record_b.average_hash)
        if a_score is not None:
            scores.append(a_score)
    if not scores:
        return 0.0
    return sum(scores) / len(scores)


def build_duplicate_groups(records: list[HashRecord], hash_type: str, threshold: float) -> list[list[HashRecord]]:
    adjacency: dict[int, set[int]] = {idx: set() for idx in range(len(records))}
    for left_idx in range(len(records)):
        for right_idx in range(left_idx + 1, len(records)):
            score = similarity(records[left_idx], records[right_idx], hash_type)
            if score >= threshold:
                adjacency[left_idx].add(right_idx)
                adjacency[right_idx].add(left_idx)

    seen: set[int] = set()
    groups: list[list[HashRecord]] = []
    for idx in range(len(records)):
        if idx in seen:
            continue
        stack = [idx]
        component: list[int] = []
        while stack:
            current = stack.pop()
            if current in seen:
                continue
            seen.add(current)
            component.append(current)
            stack.extend(adjacency[current] - seen)
        if len(component) > 1:
            group_records = [records[node] for node in component]
            groups.append(
                sorted(
                    group_records,
                    key=lambda item: (item.modified_time, len(item.path.as_posix()), item.path.as_posix()),
                )
            )
    return groups


def process_duplicates(scan_root: Path, groups: list[list[HashRecord]], dry_run: bool, move_mode: bool) -> list[dict[str, str]]:
    actions: list[dict[str, str]] = []
    duplicates_dir = scan_root / "duplicates"
    if move_mode and not dry_run:
        duplicates_dir.mkdir(parents=True, exist_ok=True)

    for group in groups:
        original = group[0]
        for duplicate in group[1:]:
            action = "dry-run"
            target_path = ""
            if not dry_run:
                if move_mode:
                    target = duplicates_dir / duplicate.path.name
                    index = 1
                    while target.exists():
                        target = duplicates_dir / f"{duplicate.path.stem}_{index}{duplicate.path.suffix}"
                        index += 1
                    shutil.move(str(duplicate.path), str(target))
                    target_path = str(target)
                    action = "moved"
                else:
                    duplicate.path.unlink(missing_ok=True)
                    action = "deleted"
            actions.append(
                {
                    "original": str(original.path),
                    "duplicate": str(duplicate.path),
                    "action": action,
                    "target": target_path,
                }
            )
    return actions


def build_report(
    scan_root: Path,
    hash_type: str,
    threshold: float,
    scanned_files: int,
    hashed_files: int,
    groups: list[list[HashRecord]],
    actions: list[dict[str, str]],
) -> dict[str, object]:
    group_payload: list[dict[str, object]] = []
    for group in groups:
        original = group[0]
        copies = []
        for dup in group[1:]:
            copies.append(
                {
                    "path": str(dup.path),
                    "size_bytes": dup.file_size,
                    "similarity_to_original": round(similarity(original, dup, hash_type), 3),
                }
            )
        group_payload.append(
            {
                "original": {
                    "path": str(original.path),
                    "size_bytes": original.file_size,
                },
                "copies": copies,
            }
        )

    return {
        "scan_path": str(scan_root),
        "created_at": datetime.now(timezone.utc).isoformat(),
        "hash_type": hash_type,
        "threshold_percent": threshold,
        "scanned_files": scanned_files,
        "hashed_files": hashed_files,
        "duplicate_groups": len(groups),
        "duplicate_files": sum(max(len(group) - 1, 0) for group in groups),
        "groups": group_payload,
        "actions": actions,
    }


def write_csv(path: Path, groups: list[list[HashRecord]], hash_type: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(
            handle,
            fieldnames=("group_id", "role", "path", "size_bytes", "similarity_to_original"),
        )
        writer.writeheader()
        for idx, group in enumerate(groups, start=1):
            original = group[0]
            writer.writerow(
                {
                    "group_id": idx,
                    "role": "original",
                    "path": str(original.path),
                    "size_bytes": original.file_size,
                    "similarity_to_original": "100.0",
                }
            )
            for duplicate in group[1:]:
                writer.writerow(
                    {
                        "group_id": idx,
                        "role": "duplicate",
                        "path": str(duplicate.path),
                        "size_bytes": duplicate.file_size,
                        "similarity_to_original": f"{similarity(original, duplicate, hash_type):.3f}",
                    }
                )


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    scan_root = args.scan.resolve()
    if not scan_root.exists() or not scan_root.is_dir():
        raise SystemExit(f"scan path must be an existing directory: {scan_root}")
    if args.threshold < 0 or args.threshold > 100:
        raise SystemExit("threshold must be between 0 and 100")
    if args.dry_run and args.move:
        print("Dry-run mode enabled: no files will be moved.")

    image_paths = iter_image_paths(scan_root)
    records: list[HashRecord] = []
    for image_path in tqdm(image_paths, desc="Hashing images", unit="file"):
        record = hash_image(image_path, args.hash_type)
        if record is not None:
            records.append(record)

    groups = build_duplicate_groups(records, args.hash_type, args.threshold)
    actions = process_duplicates(scan_root, groups, dry_run=args.dry_run, move_mode=args.move)
    report = build_report(
        scan_root=scan_root,
        hash_type=args.hash_type,
        threshold=args.threshold,
        scanned_files=len(image_paths),
        hashed_files=len(records),
        groups=groups,
        actions=actions,
    )

    args.json_out.parent.mkdir(parents=True, exist_ok=True)
    args.json_out.write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")
    write_csv(args.csv_out, groups, args.hash_type)

    print(f"Scanned files: {report['scanned_files']}")
    print(f"Hashed files: {report['hashed_files']}")
    print(f"Duplicate groups: {report['duplicate_groups']}")
    print(f"Duplicate files: {report['duplicate_files']}")
    print(f"JSON report: {args.json_out}")
    print(f"CSV report: {args.csv_out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
