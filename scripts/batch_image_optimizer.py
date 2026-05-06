#!/usr/bin/env python3
"""Bulk image optimizer with optional MiniMax enhancement."""

from __future__ import annotations

import argparse
import base64
import io
import json
import logging
import os
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable

import requests
try:
    from dotenv import load_dotenv
except ImportError:  # pragma: no cover - fallback for minimal environments
    def load_dotenv(*_: object, **__: object) -> bool:
        return False
from PIL import Image, ImageDraw, ImageFont


SUPPORTED_FORMATS = {".jpg", ".jpeg", ".png", ".webp", ".bmp"}
DEFAULT_TARGET_SIZE = (1024, 1024)
DEFAULT_QUALITY = 85
DEFAULT_MINIMAX_BASE_URL = "https://api.minimax.chat/v1"
DEFAULT_MODEL = "image-01"
LOG_PATH = Path("memory/batch_image_log.md")


def parse_size(value: str) -> tuple[int, int]:
    raw = value.lower().replace(" ", "")
    separator = "x" if "x" in raw else ","
    if separator not in raw:
        raise argparse.ArgumentTypeError("size must look like WIDTHxHEIGHT (e.g. 1024x1024)")
    width_text, height_text = raw.split(separator, 1)
    width = int(width_text)
    height = int(height_text)
    if width <= 0 or height <= 0:
        raise argparse.ArgumentTypeError("width and height must be > 0")
    return (width, height)


def parse_operations(raw_value: str) -> list[str]:
    operations = [item.strip().lower() for item in raw_value.split(",") if item.strip()]
    allowed = {"resize", "watermark", "compress", "enhance"}
    unknown = [item for item in operations if item not in allowed]
    if unknown:
        raise argparse.ArgumentTypeError(f"unsupported operations: {', '.join(unknown)}")
    return operations


def setup_logging(verbose: bool) -> logging.Logger:
    level = logging.DEBUG if verbose else logging.INFO
    logging.basicConfig(
        level=level,
        format="%(asctime)s | %(levelname)s | %(message)s",
    )
    return logging.getLogger("batch_image_optimizer")


def append_markdown_log(lines: Iterable[str]) -> None:
    LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
    with LOG_PATH.open("a", encoding="utf-8") as handle:
        handle.write("\n".join(lines))
        handle.write("\n")


def _image_to_data_uri(image: Image.Image, image_format: str = "PNG") -> str:
    buffer = io.BytesIO()
    image.save(buffer, format=image_format)
    encoded = base64.b64encode(buffer.getvalue()).decode("ascii")
    return f"data:image/{image_format.lower()};base64,{encoded}"


def _decode_minimax_image(payload: dict) -> bytes | None:
    candidates: list[str] = []
    for key in ("image_base64", "b64_json", "image"):
        value = payload.get(key)
        if isinstance(value, str) and value:
            candidates.append(value)
    for key in ("data", "images", "output"):
        value = payload.get(key)
        if isinstance(value, list):
            for item in value:
                if isinstance(item, str):
                    candidates.append(item)
                elif isinstance(item, dict):
                    for nested_key in ("b64_json", "image_base64", "image"):
                        nested_value = item.get(nested_key)
                        if isinstance(nested_value, str):
                            candidates.append(nested_value)
    for candidate in candidates:
        if candidate.startswith("data:image"):
            candidate = candidate.split(",", 1)[-1]
        try:
            return base64.b64decode(candidate, validate=False)
        except Exception:
            continue
    return None


def minimax_enhance_image(
    image: Image.Image,
    api_key: str,
    base_url: str = DEFAULT_MINIMAX_BASE_URL,
    model: str = DEFAULT_MODEL,
    timeout: int = 45,
) -> Image.Image | None:
    endpoint_candidates = (
        f"{base_url.rstrip('/')}/images/edits",
        f"{base_url.rstrip('/')}/images/generations",
    )
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }
    source_data_uri = _image_to_data_uri(image.convert("RGB"), image_format="PNG")
    payloads = (
        {
            "model": model,
            "image": source_data_uri,
            "prompt": "Enhance this image quality while preserving composition and details.",
            "size": f"{image.width}x{image.height}",
        },
        {
            "model": model,
            "prompt": "Upscale and enhance this image while preserving content.",
            "image": source_data_uri,
        },
    )

    last_error: str | None = None
    for endpoint in endpoint_candidates:
        for payload in payloads:
            try:
                response = requests.post(endpoint, headers=headers, json=payload, timeout=timeout)
            except requests.RequestException as exc:
                last_error = str(exc)
                continue
            if response.status_code >= 400:
                last_error = f"{response.status_code}: {response.text[:300]}"
                continue
            try:
                parsed = response.json()
            except json.JSONDecodeError:
                last_error = "MiniMax response is not valid JSON"
                continue
            decoded = _decode_minimax_image(parsed)
            if not decoded:
                last_error = f"MiniMax response had no decodable image fields: {list(parsed.keys())}"
                continue
            try:
                enhanced = Image.open(io.BytesIO(decoded))
                return enhanced.convert("RGB")
            except OSError as exc:
                last_error = f"cannot open MiniMax output image: {exc}"
                continue

    if last_error:
        raise RuntimeError(last_error)
    return None


def apply_resize(image: Image.Image, target_size: tuple[int, int]) -> Image.Image:
    return image.resize(target_size, Image.Resampling.LANCZOS)


def apply_watermark(
    image: Image.Image,
    text: str,
    opacity: int = 90,
    margin: int = 24,
) -> Image.Image:
    if not text:
        return image
    base = image.convert("RGBA")
    overlay = Image.new("RGBA", base.size, (255, 255, 255, 0))
    draw = ImageDraw.Draw(overlay)
    font = ImageFont.load_default()
    text_bbox = draw.textbbox((0, 0), text, font=font)
    text_width = text_bbox[2] - text_bbox[0]
    text_height = text_bbox[3] - text_bbox[1]
    x = max(margin, base.width - text_width - margin)
    y = max(margin, base.height - text_height - margin)
    alpha = max(0, min(opacity, 255))
    draw.text((x, y), text, font=font, fill=(255, 255, 255, alpha))
    merged = Image.alpha_composite(base, overlay)
    return merged.convert("RGB")


def single_image_process(
    image_path: Path,
    operations: list[str],
    target_size: tuple[int, int] = DEFAULT_TARGET_SIZE,
    watermark_text: str = "",
    minimax_api_key: str | None = None,
    minimax_base_url: str = DEFAULT_MINIMAX_BASE_URL,
    minimax_model: str = DEFAULT_MODEL,
    logger: logging.Logger | None = None,
) -> tuple[Image.Image, list[str]]:
    logger = logger or logging.getLogger("batch_image_optimizer")
    steps: list[str] = []
    with Image.open(image_path) as source:
        image = source.convert("RGB")

    for operation in operations:
        if operation == "resize":
            image = apply_resize(image, target_size)
            steps.append(f"resize->{target_size[0]}x{target_size[1]}")
        elif operation == "watermark":
            if watermark_text:
                image = apply_watermark(image, watermark_text)
                steps.append("watermark")
            else:
                steps.append("watermark(skipped:no-text)")
        elif operation == "compress":
            # Compression is applied at save-time with quality + metadata stripping.
            steps.append(f"compress(quality={DEFAULT_QUALITY})")
        elif operation == "enhance":
            if not minimax_api_key:
                logger.warning("MINIMAX_API_KEY not set; enhancement skipped for %s", image_path.name)
                steps.append("enhance(skipped:no-api-key)")
                continue
            try:
                enhanced = minimax_enhance_image(
                    image=image,
                    api_key=minimax_api_key,
                    base_url=minimax_base_url,
                    model=minimax_model,
                )
                if enhanced:
                    image = enhanced
                    steps.append("enhance")
                else:
                    steps.append("enhance(skipped:no-output)")
            except Exception as exc:
                logger.warning("MiniMax enhancement failed for %s: %s", image_path.name, exc)
                steps.append(f"enhance(skipped:error:{exc})")
        else:
            raise ValueError(f"Unknown operation: {operation}")

    return image, steps


def process_directory(
    input_dir: Path | str,
    output_dir: Path | str,
    operations: list[str] | None = None,
    target_size: tuple[int, int] = DEFAULT_TARGET_SIZE,
    watermark_text: str = "",
    minimax_api_key: str | None = None,
    minimax_base_url: str = DEFAULT_MINIMAX_BASE_URL,
    minimax_model: str = DEFAULT_MODEL,
    logger: logging.Logger | None = None,
) -> dict[str, int]:
    logger = logger or logging.getLogger("batch_image_optimizer")
    operations = operations or ["resize", "watermark", "compress"]
    in_dir = Path(input_dir)
    out_dir = Path(output_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    image_paths = sorted(
        path
        for path in in_dir.iterdir()
        if path.is_file() and path.suffix.lower() in SUPPORTED_FORMATS
    )
    if not image_paths:
        logger.warning("No supported images found in %s", in_dir)
        return {"processed": 0, "skipped": 0, "failed": 0}

    processed = 0
    failed = 0
    log_rows = [
        f"## Batch run {datetime.now(timezone.utc).isoformat()}",
        f"- Input: `{in_dir}`",
        f"- Output: `{out_dir}`",
        f"- Operations: `{operations}`",
        "",
        "| File | Status | Steps |",
        "|---|---|---|",
    ]

    for image_path in image_paths:
        output_path = out_dir / image_path.name
        try:
            image, steps = single_image_process(
                image_path=image_path,
                operations=operations,
                target_size=target_size,
                watermark_text=watermark_text,
                minimax_api_key=minimax_api_key,
                minimax_base_url=minimax_base_url,
                minimax_model=minimax_model,
                logger=logger,
            )
            save_kwargs = {
                "optimize": True,
                "quality": DEFAULT_QUALITY,
            }
            if image_path.suffix.lower() in {".png", ".bmp"}:
                output_path = output_path.with_suffix(".png")
                save_kwargs.pop("quality", None)
            elif image_path.suffix.lower() == ".webp":
                output_path = output_path.with_suffix(".webp")
                save_kwargs["method"] = 6
            else:
                output_path = output_path.with_suffix(".jpg")
                image = image.convert("RGB")
            image.save(output_path, exif=b"", **save_kwargs)
            processed += 1
            log_rows.append(f"| `{image_path.name}` | ok | `{' -> '.join(steps)}` |")
            logger.info("Processed: %s -> %s", image_path.name, output_path.name)
        except Exception as exc:
            failed += 1
            logger.exception("Failed to process %s: %s", image_path.name, exc)
            log_rows.append(f"| `{image_path.name}` | failed | `{exc}` |")

    append_markdown_log(log_rows + [""])
    return {"processed": processed, "skipped": 0, "failed": failed}


def run_self_test(logger: logging.Logger) -> int:
    logger.info("Running self-test with mock images")
    with tempfile.TemporaryDirectory(prefix="batch-image-optimizer-") as tmp_dir:
        root = Path(tmp_dir)
        input_dir = root / "input"
        output_dir = root / "output"
        input_dir.mkdir(parents=True, exist_ok=True)

        mock_specs = [
            ("mock_red.jpg", (1600, 1200), (220, 40, 40)),
            ("mock_green.png", (1200, 1200), (40, 200, 40)),
            ("mock_blue.webp", (900, 1600), (40, 40, 220)),
            ("mock_gray.bmp", (800, 800), (120, 120, 120)),
        ]
        for filename, size, color in mock_specs:
            image = Image.new("RGB", size, color)
            image.save(input_dir / filename)

        stats = process_directory(
            input_dir=input_dir,
            output_dir=output_dir,
            operations=["resize", "watermark", "compress"],
            target_size=(1024, 1024),
            watermark_text="SELFTEST",
            minimax_api_key=None,
            logger=logger,
        )
        outputs = list(output_dir.iterdir())
        if stats["processed"] != len(mock_specs) or len(outputs) != len(mock_specs):
            logger.error("Self-test failed: stats=%s outputs=%s", stats, len(outputs))
            return 1
        logger.info("Self-test passed: %s files generated", len(outputs))
    return 0


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Bulk image processor (resize/watermark/compress/enhance via MiniMax).",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    run_parser = subparsers.add_parser("run", help="Process a directory of images.")
    run_parser.add_argument("--input-dir", required=True, type=Path)
    run_parser.add_argument("--output-dir", required=True, type=Path)
    run_parser.add_argument(
        "--operations",
        type=parse_operations,
        default=["resize", "watermark", "compress"],
        help="Comma-separated operations list.",
    )
    run_parser.add_argument("--target-size", type=parse_size, default=DEFAULT_TARGET_SIZE)
    run_parser.add_argument("--watermark-text", default="")
    run_parser.add_argument("--minimax-base-url", default=DEFAULT_MINIMAX_BASE_URL)
    run_parser.add_argument("--minimax-model", default=DEFAULT_MODEL)
    run_parser.add_argument("--verbose", action="store_true")

    test_parser = subparsers.add_parser("self-test", help="Run a mock-image self test.")
    test_parser.add_argument("--verbose", action="store_true")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    load_dotenv()
    args = parse_args(argv)
    logger = setup_logging(getattr(args, "verbose", False))
    minimax_api_key = os.getenv("MINIMAX_API_KEY")

    if args.command == "self-test":
        return run_self_test(logger)

    if args.command == "run":
        stats = process_directory(
            input_dir=args.input_dir,
            output_dir=args.output_dir,
            operations=args.operations,
            target_size=args.target_size,
            watermark_text=args.watermark_text,
            minimax_api_key=minimax_api_key,
            minimax_base_url=args.minimax_base_url,
            minimax_model=args.minimax_model,
            logger=logger,
        )
        logger.info("Batch completed: %s", stats)
        return 0 if stats["failed"] == 0 else 2

    return 1


if __name__ == "__main__":
    sys.exit(main())
