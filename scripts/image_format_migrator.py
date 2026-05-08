#!/usr/bin/env python3
"""Convert and compress image archives to JPEG."""

from __future__ import annotations

import argparse
import io
import logging
import shutil
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

from PIL import Image, UnidentifiedImageError

try:
    import magic  # type: ignore
except ImportError:  # pragma: no cover
    magic = None

try:
    import pyheif  # type: ignore
except ImportError:  # pragma: no cover
    pyheif = None


SUPPORTED_EXTENSIONS = {".png", ".tif", ".tiff", ".bmp", ".webp", ".heic", ".jpg", ".jpeg"}
CONVERT_EXTENSIONS = {".png", ".tif", ".tiff", ".bmp", ".webp", ".heic"}
DEFAULT_QUALITY = 85
PROGRESS_BAR_WIDTH = 24
LOG_EVERY_N_FILES = 100


@dataclass
class FileResult:
    source: Path
    destination: Path
    converted: bool
    dry_run: bool


def setup_logger() -> logging.Logger:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s | %(levelname)s | %(message)s",
    )
    return logging.getLogger("image_format_migrator")


def detect_mime(path: Path) -> str | None:
    if magic is None:
        return None
    try:
        detector = magic.Magic(mime=True)
        return detector.from_file(str(path))
    except Exception:
        return None


def is_supported_image(path: Path) -> bool:
    if path.suffix.lower() in SUPPORTED_EXTENSIONS:
        return True
    mime = detect_mime(path)
    return bool(mime and mime.startswith("image/"))


def iter_images(root: Path) -> Iterable[Path]:
    for path in root.rglob("*"):
        if path.is_file() and is_supported_image(path):
            yield path


def heic_to_image(path: Path) -> Image.Image:
    if pyheif is None:
        raise RuntimeError("pyheif is required for HEIC files")
    heif = pyheif.read_heif(path.read_bytes())
    image = Image.frombytes(
        heif.mode,
        heif.size,
        heif.data,
        "raw",
        heif.mode,
        heif.stride,
    )
    return image


def open_image(path: Path) -> tuple[Image.Image, bytes | None]:
    suffix = path.suffix.lower()
    if suffix == ".heic":
        image = heic_to_image(path)
        return image.convert("RGB"), None

    with Image.open(path) as source:
        exif_data = source.info.get("exif")
        image = source.convert("RGB")
    return image, exif_data if isinstance(exif_data, bytes) else None


def destination_for_file(source: Path, overwrite: bool, output_root: Path | None) -> Path:
    if overwrite:
        return source.with_suffix(".jpg")

    if output_root is None:
        return source.with_name(f"{source.stem}_converted.jpg")

    output_root.mkdir(parents=True, exist_ok=True)
    return output_root / f"{source.stem}.jpg"


def convert_file(
    source: Path,
    *,
    quality: int,
    preserve_exif: bool,
    dry_run: bool,
    overwrite: bool,
    output_root: Path | None,
) -> FileResult:
    destination = destination_for_file(source, overwrite=overwrite, output_root=output_root)
    converted = source.suffix.lower() in CONVERT_EXTENSIONS

    if dry_run:
        return FileResult(source=source, destination=destination, converted=converted, dry_run=True)

    image, exif_data = open_image(source)
    destination.parent.mkdir(parents=True, exist_ok=True)
    save_kwargs: dict[str, object] = {"format": "JPEG", "quality": quality, "optimize": True}
    if preserve_exif and exif_data:
        save_kwargs["exif"] = exif_data
    image.save(destination, **save_kwargs)

    if overwrite and source != destination and source.exists():
        source.unlink()

    return FileResult(source=source, destination=destination, converted=converted, dry_run=False)


def default_output_dir(scan_path: Path) -> Path:
    return scan_path.parent / f"{scan_path.name}_converted"


def print_progress(current: int, total: int) -> None:
    if total <= 0:
        return
    ratio = current / total
    done = int(ratio * PROGRESS_BAR_WIDTH)
    bar = "#" * done + "-" * (PROGRESS_BAR_WIDTH - done)
    sys.stdout.write(f"\r[{bar}] {current}/{total} ({ratio * 100:5.1f}%)")
    if current >= total:
        sys.stdout.write("\n")
    sys.stdout.flush()


def process_many(
    files: list[Path],
    *,
    quality: int,
    preserve_exif: bool,
    dry_run: bool,
    overwrite: bool,
    output_root: Path | None,
    logger: logging.Logger,
) -> int:
    if not files:
        logger.warning("No supported files found")
        return 0

    processed = 0
    failures = 0
    for index, source in enumerate(files, start=1):
        try:
            result = convert_file(
                source,
                quality=quality,
                preserve_exif=preserve_exif,
                dry_run=dry_run,
                overwrite=overwrite,
                output_root=output_root,
            )
            processed += 1
            if index % LOG_EVERY_N_FILES == 0:
                logger.info(
                    "Progress: %s files processed, latest: %s -> %s",
                    index,
                    result.source,
                    result.destination,
                )
        except (OSError, UnidentifiedImageError, RuntimeError) as exc:
            failures += 1
            logger.error("Failed to process %s: %s", source, exc)
        print_progress(index, len(files))

    logger.info("Done: processed=%s failed=%s total=%s", processed, failures, len(files))
    return 0 if failures == 0 else 2


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Convert/compress image archives to JPEG with optional EXIF preservation.",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--scan", type=Path, help="Recursively scan folder and convert supported images.")
    mode.add_argument("--single", type=Path, help="Convert single file and save next to source by default.")
    parser.add_argument("--output", type=Path, default=None, help="Output directory.")
    parser.add_argument("--quality", type=int, default=DEFAULT_QUALITY, help="JPEG quality [1..95].")
    parser.add_argument("--preserve-exif", action=argparse.BooleanOptionalAction, default=True)
    parser.add_argument("--dry-run", action="store_true", help="Preview only, no file writes.")
    parser.add_argument("--overwrite", action="store_true", help="Replace originals with JPEG outputs.")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    logger = setup_logger()

    quality = max(1, min(int(args.quality), 95))
    if quality != args.quality:
        logger.warning("Quality adjusted to %s (allowed range 1..95)", quality)

    if args.single:
        source = args.single
        if not source.exists() or not source.is_file():
            logger.error("Single file does not exist: %s", source)
            return 1
        output_root = args.output
        result = convert_file(
            source,
            quality=quality,
            preserve_exif=args.preserve_exif,
            dry_run=args.dry_run,
            overwrite=args.overwrite,
            output_root=output_root,
        )
        logger.info("Single file processed: %s -> %s", result.source, result.destination)
        return 0

    scan_path: Path = args.scan
    if not scan_path.exists() or not scan_path.is_dir():
        logger.error("Scan path must be an existing directory: %s", scan_path)
        return 1

    output_root = args.output
    if output_root is None and not args.overwrite:
        output_root = default_output_dir(scan_path)
    if args.overwrite:
        output_root = None
    files = sorted(iter_images(scan_path))
    return process_many(
        files,
        quality=quality,
        preserve_exif=args.preserve_exif,
        dry_run=args.dry_run,
        overwrite=args.overwrite,
        output_root=output_root,
        logger=logger,
    )


if __name__ == "__main__":
    sys.exit(main())
