#!/usr/bin/env python3
"""Batch image processing utility for OpenClaw orchestration."""

from __future__ import annotations

import argparse
import pathlib
import sys
from typing import Iterable

from PIL import Image, ImageOps

SUPPORTED_EXTS = {".jpg", ".jpeg", ".png", ".webp"}


def iter_images(input_dir: pathlib.Path) -> Iterable[pathlib.Path]:
    for path in sorted(input_dir.rglob("*")):
        if path.is_file() and path.suffix.lower() in SUPPORTED_EXTS:
            yield path


def process_image(
    image_path: pathlib.Path,
    output_path: pathlib.Path,
    *,
    max_width: int,
    quality: int,
) -> None:
    with Image.open(image_path) as img:
        img = ImageOps.exif_transpose(img)
        if img.width > max_width:
            ratio = max_width / float(img.width)
            target_size = (max_width, int(img.height * ratio))
            img = img.resize(target_size, Image.Resampling.LANCZOS)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        save_kwargs = {"optimize": True}
        if output_path.suffix.lower() in {".jpg", ".jpeg", ".webp"}:
            save_kwargs["quality"] = quality
        img.save(output_path, **save_kwargs)


def main() -> int:
    parser = argparse.ArgumentParser(description="Process images in batch mode.")
    parser.add_argument("--input", required=True, type=pathlib.Path, help="Input directory")
    parser.add_argument("--output", required=True, type=pathlib.Path, help="Output directory")
    parser.add_argument("--max-width", type=int, default=1280, help="Maximum output width")
    parser.add_argument("--quality", type=int, default=85, help="Quality for lossy formats")
    args = parser.parse_args()

    if not args.input.exists():
        print(f"[ERROR] Input directory does not exist: {args.input}", file=sys.stderr)
        return 1
    if not args.input.is_dir():
        print(f"[ERROR] Input path is not a directory: {args.input}", file=sys.stderr)
        return 1
    if args.max_width <= 0:
        print("[ERROR] --max-width must be > 0", file=sys.stderr)
        return 1
    if args.quality < 1 or args.quality > 100:
        print("[ERROR] --quality must be between 1 and 100", file=sys.stderr)
        return 1

    images = list(iter_images(args.input))
    if not images:
        print("[WARN] No supported images found.")
        return 0

    processed = 0
    for source in images:
        rel = source.relative_to(args.input)
        destination = args.output / rel
        process_image(
            source,
            destination,
            max_width=args.max_width,
            quality=args.quality,
        )
        processed += 1
        print(f"[OK] {source} -> {destination}")

    print(f"[DONE] Processed {processed} image(s).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
