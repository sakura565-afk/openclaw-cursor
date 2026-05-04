#!/usr/bin/env python3
"""Practical media processing helpers for the OpenClaw workflow."""

from __future__ import annotations

import argparse
import io
import sys
import tempfile
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from pathlib import Path
from typing import Sequence

from PIL import Image, ImageOps, UnidentifiedImageError

TELEGRAM_MAX_BYTES = 10 * 1024 * 1024
TELEGRAM_MAX_SIDE = 3840
THUMB_MAX_SIDE = 300
DEFAULT_BATCH_WORKERS = 4
MAX_BATCH_WORKERS = 8
ORIENTATION_TAG = 274

FORMAT_ALIASES = {
    "jpg": "JPEG",
    "jpeg": "JPEG",
    "png": "PNG",
    "webp": "WEBP",
}

FORMAT_SUFFIXES = {
    "JPEG": ".jpg",
    "PNG": ".png",
    "WEBP": ".webp",
}

LOSSY_FORMATS = {"JPEG", "WEBP"}
EXIF_CAPABLE_FORMATS = {"JPEG", "PNG", "WEBP"}
RESAMPLING = Image.Resampling.LANCZOS


def stderr(message: str) -> None:
    print(message, file=sys.stderr)


def positive_int(value: str) -> int:
    parsed = int(value)
    if parsed <= 0:
        raise argparse.ArgumentTypeError("value must be greater than zero")
    return parsed


def quality_value(value: str) -> int:
    parsed = int(value)
    if not 1 <= parsed <= 100:
        raise argparse.ArgumentTypeError("quality must be between 1 and 100")
    return parsed


def worker_count(value: str) -> int:
    parsed = positive_int(value)
    if parsed > MAX_BATCH_WORKERS:
        raise argparse.ArgumentTypeError(
            f"workers must be between 1 and {MAX_BATCH_WORKERS}"
        )
    return parsed


def load_image(source: str) -> tuple[Image.Image, str | None]:
    if source == "-":
        image_bytes = sys.stdin.buffer.read()
        if not image_bytes:
            raise ValueError("no input image bytes received on stdin")
        buffer = io.BytesIO(image_bytes)
        image = Image.open(buffer)
    else:
        image = Image.open(source)
    image.load()
    return image, image.format


def normalize_image(image: Image.Image) -> Image.Image:
    normalized = ImageOps.exif_transpose(image)
    normalized.info = dict(image.info)
    return normalized


def collect_metadata(image: Image.Image) -> dict[str, bytes]:
    metadata: dict[str, bytes] = {}
    exif = image.getexif()
    if exif:
        exif[ORIENTATION_TAG] = 1
        exif_bytes = exif.tobytes()
        if exif_bytes:
            metadata["exif"] = exif_bytes

    icc_profile = image.info.get("icc_profile")
    if icc_profile:
        metadata["icc_profile"] = icc_profile

    return metadata


def resolve_format(
    output_path: str,
    requested_format: str | None,
    source_format: str | None,
) -> str:
    if requested_format:
        key = requested_format.lower()
        if key not in FORMAT_ALIASES:
            raise ValueError(
                f"unsupported format '{requested_format}', choose from: jpeg, png, webp"
            )
        return FORMAT_ALIASES[key]

    if output_path != "-":
        suffix = Path(output_path).suffix.lower().lstrip(".")
        if suffix in FORMAT_ALIASES:
            return FORMAT_ALIASES[suffix]

    if source_format:
        return source_format.upper()

    raise ValueError("unable to determine output format, use --format")


def ensure_mode(image: Image.Image, output_format: str) -> Image.Image:
    if output_format == "JPEG":
        if image.mode in {"RGBA", "LA"} or (
            image.mode == "P" and "transparency" in image.info
        ):
            rgba = image.convert("RGBA")
            flattened = Image.new("RGB", rgba.size, (255, 255, 255))
            flattened.paste(rgba, mask=rgba.getchannel("A"))
            return flattened
        if image.mode not in {"RGB", "L", "CMYK"}:
            return image.convert("RGB")
        return image

    if output_format in {"PNG", "WEBP"} and image.mode not in {
        "RGB",
        "RGBA",
        "L",
        "LA",
        "P",
    }:
        if "A" in image.getbands():
            return image.convert("RGBA")
        return image.convert("RGB")

    return image


def quality_candidates(start_quality: int) -> list[int]:
    candidates: list[int] = []
    for value in range(start_quality, 24, -5):
        candidates.append(value)
    for fallback in (25, 20, 15, 10):
        if fallback not in candidates:
            candidates.append(fallback)
    return candidates


def render_image(
    image: Image.Image,
    output_format: str,
    *,
    quality: int,
    metadata: dict[str, bytes],
) -> bytes:
    prepared = ensure_mode(image, output_format)
    output = io.BytesIO()
    save_kwargs: dict[str, object] = {}

    if output_format == "JPEG":
        save_kwargs.update(quality=quality, optimize=True, progressive=True)
    elif output_format == "WEBP":
        save_kwargs.update(quality=quality, method=6)
    elif output_format == "PNG":
        save_kwargs.update(optimize=True, compress_level=9)

    if output_format in EXIF_CAPABLE_FORMATS and metadata.get("exif"):
        save_kwargs["exif"] = metadata["exif"]
    if metadata.get("icc_profile"):
        save_kwargs["icc_profile"] = metadata["icc_profile"]

    prepared.save(output, format=output_format, **save_kwargs)
    return output.getvalue()


def resize_longest_side(image: Image.Image, max_side: int) -> Image.Image:
    longest_side = max(image.size)
    if longest_side <= max_side:
        return image.copy()
    scale = max_side / float(longest_side)
    new_size = (
        max(1, int(round(image.width * scale))),
        max(1, int(round(image.height * scale))),
    )
    return image.resize(new_size, RESAMPLING)


def make_thumbnail(image: Image.Image, max_side: int) -> Image.Image:
    thumbnail = image.copy()
    thumbnail.thumbnail((max_side, max_side), RESAMPLING)
    return thumbnail


def enforce_telegram_limits(
    image: Image.Image,
    output_format: str,
    *,
    quality: int,
    max_side: int,
    max_bytes: int,
    metadata: dict[str, bytes],
) -> bytes:
    working = resize_longest_side(image, max_side)
    last_best = b""

    for _ in range(12):
        qualities = quality_candidates(quality) if output_format in LOSSY_FORMATS else [quality]
        smallest_attempt: bytes | None = None

        for candidate_quality in qualities:
            rendered = render_image(
                working,
                output_format,
                quality=candidate_quality,
                metadata=metadata,
            )
            if smallest_attempt is None or len(rendered) < len(smallest_attempt):
                smallest_attempt = rendered
            if len(rendered) <= max_bytes:
                return rendered

        if not smallest_attempt:
            break

        last_best = smallest_attempt
        if max(working.size) <= 1:
            break

        shrink_ratio = (max_bytes / float(len(smallest_attempt))) ** 0.5 * 0.97
        shrink_ratio = min(0.95, max(0.5, shrink_ratio))
        new_size = (
            max(1, int(working.width * shrink_ratio)),
            max(1, int(working.height * shrink_ratio)),
        )
        if new_size == working.size:
            new_size = (max(1, working.width - 1), max(1, working.height - 1))
        working = working.resize(new_size, RESAMPLING)

    if last_best:
        return last_best

    raise RuntimeError("unable to render output image")


def atomic_write_bytes(destination: Path, data: bytes) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary_path: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            dir=destination.parent,
            prefix=f".{destination.name}.",
            suffix=".tmp",
            delete=False,
        ) as handle:
            handle.write(data)
            temporary_path = Path(handle.name)
        temporary_path.replace(destination)
    except Exception:
        if temporary_path is not None:
            temporary_path.unlink(missing_ok=True)
        raise


def write_output(data: bytes, destination: str) -> None:
    if destination == "-":
        sys.stdout.buffer.write(data)
        sys.stdout.buffer.flush()
        return

    destination_path = Path(destination)
    atomic_write_bytes(destination_path, data)


def process_image(
    image: Image.Image,
    output_format: str,
    *,
    quality: int,
    operation: str,
    thumbnail_size: int = THUMB_MAX_SIDE,
    max_side: int = TELEGRAM_MAX_SIDE,
    max_bytes: int = TELEGRAM_MAX_BYTES,
) -> bytes:
    normalized = normalize_image(image)
    metadata = collect_metadata(normalized)

    if operation == "resize":
        return enforce_telegram_limits(
            normalized,
            output_format,
            quality=quality,
            max_side=max_side,
            max_bytes=max_bytes,
            metadata=metadata,
        )

    if operation == "thumb":
        transformed = make_thumbnail(normalized, thumbnail_size)
    elif operation == "compress":
        transformed = normalized.copy()
    elif operation == "convert":
        transformed = normalized.copy()
    else:
        raise ValueError(f"unsupported operation '{operation}'")

    return render_image(
        transformed,
        output_format,
        quality=quality,
        metadata=metadata,
    )


def process_single(
    input_path: str,
    output_path: str,
    *,
    requested_format: str | None,
    quality: int,
    operation: str,
    thumbnail_size: int = THUMB_MAX_SIDE,
    max_side: int = TELEGRAM_MAX_SIDE,
    max_bytes: int = TELEGRAM_MAX_BYTES,
) -> bytes:
    image, source_format = load_image(input_path)
    try:
        output_format = resolve_format(output_path, requested_format, source_format)
        return process_image(
            image,
            output_format,
            quality=quality,
            operation=operation,
            thumbnail_size=thumbnail_size,
            max_side=max_side,
            max_bytes=max_bytes,
        )
    finally:
        image.close()


def handle_single_command(args: argparse.Namespace) -> int:
    rendered = process_single(
        args.input,
        args.output,
        requested_format=args.format,
        quality=args.quality,
        operation=args.command,
        thumbnail_size=getattr(args, "size", THUMB_MAX_SIDE),
        max_side=getattr(args, "max_side", TELEGRAM_MAX_SIDE),
        max_bytes=getattr(args, "max_bytes", TELEGRAM_MAX_BYTES),
    )
    write_output(rendered, args.output)
    return 0


def iter_batch_inputs(values: Sequence[str]) -> list[str]:
    if values:
        return list(values)

    batch_values = [line.strip() for line in sys.stdin if line.strip()]
    if not batch_values:
        raise ValueError("batch requires input files or newline-delimited paths on stdin")
    return batch_values


def build_batch_destination(
    input_path: str,
    output_dir: Path,
    output_format: str,
) -> Path:
    input_name = Path(input_path).stem
    suffix = FORMAT_SUFFIXES.get(output_format, f".{output_format.lower()}")
    return output_dir / f"{input_name}{suffix}"


@dataclass(frozen=True, slots=True)
class BatchJob:
    index: int
    total: int
    input_path: str
    output_dir: Path
    requested_format: str | None
    quality: int
    operation: str
    thumbnail_size: int
    max_side: int
    max_bytes: int


@dataclass(frozen=True, slots=True)
class BatchResult:
    index: int
    total: int
    input_path: str
    destination: Path
    rendered: bytes
    operation: str


def process_batch_job(job: BatchJob) -> BatchResult:
    image, source_format = load_image(job.input_path)
    try:
        output_format = resolve_format("-", job.requested_format, source_format)
        destination = build_batch_destination(job.input_path, job.output_dir, output_format)
        rendered = process_image(
            image,
            output_format,
            quality=job.quality,
            operation=job.operation,
            thumbnail_size=job.thumbnail_size,
            max_side=job.max_side,
            max_bytes=job.max_bytes,
        )
    finally:
        image.close()

    return BatchResult(
        index=job.index,
        total=job.total,
        input_path=job.input_path,
        destination=destination,
        rendered=rendered,
        operation=job.operation,
    )


def handle_batch_command(args: argparse.Namespace) -> int:
    input_paths = iter_batch_inputs(args.inputs)
    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    total = len(input_paths)

    jobs = [
        BatchJob(
            index=index,
            total=total,
            input_path=input_path,
            output_dir=output_dir,
            requested_format=args.format,
            quality=args.quality,
            operation=args.operation,
            thumbnail_size=args.size,
            max_side=args.max_side,
            max_bytes=args.max_bytes,
        )
        for index, input_path in enumerate(input_paths, start=1)
    ]
    parallel_workers = min(args.workers, total)

    if parallel_workers == 1:
        results = map(process_batch_job, jobs)
        for result in results:
            stderr(
                f"[{result.index}/{result.total}] {result.operation} "
                f"{result.input_path} -> {result.destination}"
            )
            write_output(result.rendered, str(result.destination))
            print(result.destination)
        return 0

    with ThreadPoolExecutor(max_workers=parallel_workers) as executor:
        for result in executor.map(process_batch_job, jobs):
            stderr(
                f"[{result.index}/{result.total}] {result.operation} "
                f"{result.input_path} -> {result.destination}"
            )
            # Serialize writes in the main thread so concurrent workers never
            # contend over the filesystem while still rendering in parallel.
            write_output(result.rendered, str(result.destination))
            print(result.destination)

    return 0


def add_shared_output_options(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "input",
        nargs="?",
        default="-",
        help="input image path or '-' for stdin",
    )
    parser.add_argument(
        "output",
        nargs="?",
        default="-",
        help="output image path or '-' for stdout",
    )
    parser.add_argument(
        "--format",
        choices=sorted(FORMAT_ALIASES),
        help="explicit output format, required when writing to stdout without a source format",
    )
    parser.add_argument(
        "--quality",
        type=quality_value,
        default=85,
        help="JPEG/WebP quality (1-100, default: 85)",
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="OpenClaw media processing CLI",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    resize_parser = subparsers.add_parser(
        "resize",
        help="resize images to Telegram-safe limits",
    )
    add_shared_output_options(resize_parser)
    resize_parser.add_argument(
        "--max-side",
        type=positive_int,
        default=TELEGRAM_MAX_SIDE,
        help=f"maximum pixels on the longest side (default: {TELEGRAM_MAX_SIDE})",
    )
    resize_parser.add_argument(
        "--max-bytes",
        type=positive_int,
        default=TELEGRAM_MAX_BYTES,
        help=f"maximum output size in bytes (default: {TELEGRAM_MAX_BYTES})",
    )
    resize_parser.set_defaults(handler=handle_single_command)

    thumb_parser = subparsers.add_parser(
        "thumb",
        help="create a thumbnail with preserved aspect ratio",
    )
    add_shared_output_options(thumb_parser)
    thumb_parser.add_argument(
        "--size",
        type=positive_int,
        default=THUMB_MAX_SIDE,
        help=f"maximum thumbnail side in pixels (default: {THUMB_MAX_SIDE})",
    )
    thumb_parser.set_defaults(handler=handle_single_command)

    compress_parser = subparsers.add_parser(
        "compress",
        help="compress an image without changing dimensions",
    )
    add_shared_output_options(compress_parser)
    compress_parser.set_defaults(handler=handle_single_command)

    convert_parser = subparsers.add_parser(
        "convert",
        help="convert image formats",
    )
    add_shared_output_options(convert_parser)
    convert_parser.set_defaults(handler=handle_single_command)

    batch_parser = subparsers.add_parser(
        "batch",
        help="run an operation over many input images",
    )
    batch_parser.add_argument(
        "output_dir",
        help="directory to write processed files into",
    )
    batch_parser.add_argument(
        "inputs",
        nargs="*",
        help="input files, or provide newline-delimited paths on stdin",
    )
    batch_parser.add_argument(
        "--operation",
        choices=("resize", "thumb", "compress", "convert"),
        default="compress",
        help="operation to apply to each input (default: compress)",
    )
    batch_parser.add_argument(
        "--format",
        choices=sorted(FORMAT_ALIASES),
        help="explicit output format for all batch files",
    )
    batch_parser.add_argument(
        "--quality",
        type=quality_value,
        default=85,
        help="JPEG/WebP quality (1-100, default: 85)",
    )
    batch_parser.add_argument(
        "--size",
        type=positive_int,
        default=THUMB_MAX_SIDE,
        help=f"thumbnail size for thumb operations (default: {THUMB_MAX_SIDE})",
    )
    batch_parser.add_argument(
        "--max-side",
        type=positive_int,
        default=TELEGRAM_MAX_SIDE,
        help=f"maximum pixels on the longest side for resize (default: {TELEGRAM_MAX_SIDE})",
    )
    batch_parser.add_argument(
        "--max-bytes",
        type=positive_int,
        default=TELEGRAM_MAX_BYTES,
        help=f"maximum output size in bytes for resize (default: {TELEGRAM_MAX_BYTES})",
    )
    batch_parser.add_argument(
        "--workers",
        type=worker_count,
        default=DEFAULT_BATCH_WORKERS,
        help=(
            "number of parallel workers to use for batch processing "
            f"(default: {DEFAULT_BATCH_WORKERS}, max: {MAX_BATCH_WORKERS})"
        ),
    )
    batch_parser.set_defaults(handler=handle_batch_command)

    return parser


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        return args.handler(args)
    except (OSError, RuntimeError, UnidentifiedImageError, ValueError) as exc:
        stderr(f"error: {exc}")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
