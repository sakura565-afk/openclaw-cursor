from __future__ import annotations

import argparse
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

from PIL import Image, ImageChops


SUPPORTED_IMAGE_SUFFIXES = {
    ".bmp",
    ".gif",
    ".jpeg",
    ".jpg",
    ".png",
    ".ppm",
    ".tif",
    ".tiff",
    ".webp",
}


@dataclass(frozen=True)
class ProcessingResult:
    input_path: Path
    output_path: Path
    original_size_bytes: int
    processed_size_bytes: int
    width: int
    height: int


def build_alpha_gradient(height: int, fade_strength: float) -> Image.Image:
    if height <= 0:
        raise ValueError("Image height must be greater than zero.")

    scale = Image.new("L", (1, height))
    if height == 1:
        alpha_values = [255]
    else:
        alpha_values = [
            int(255 * (1 - (fade_strength * (row / (height - 1)))))
            for row in range(height)
        ]
    scale.putdata(alpha_values)
    return scale


def apply_fade(image: Image.Image, fade_strength: float = 0.55) -> Image.Image:
    if not 0 <= fade_strength <= 1:
        raise ValueError("fade_strength must be between 0 and 1.")

    faded = image.convert("RGBA")
    width, height = faded.size
    alpha_gradient = build_alpha_gradient(height, fade_strength).resize((width, height))
    combined_alpha = ImageChops.multiply(faded.getchannel("A"), alpha_gradient)
    faded.putalpha(combined_alpha)
    return faded


def iter_image_files(input_dir: Path) -> Iterable[Path]:
    for file_path in sorted(input_dir.iterdir()):
        if file_path.is_file() and file_path.suffix.lower() in SUPPORTED_IMAGE_SUFFIXES:
            yield file_path


def process_image(
    input_path: Path,
    output_dir: Path,
    fade_strength: float = 0.55,
) -> ProcessingResult:
    output_dir.mkdir(parents=True, exist_ok=True)
    output_path = output_dir / f"{input_path.stem}-faded.png"

    with Image.open(input_path) as source_image:
        width, height = source_image.size
        processed_image = apply_fade(source_image, fade_strength=fade_strength)
        processed_image.save(output_path, format="PNG", optimize=True)

    return ProcessingResult(
        input_path=input_path,
        output_path=output_path,
        original_size_bytes=input_path.stat().st_size,
        processed_size_bytes=output_path.stat().st_size,
        width=width,
        height=height,
    )


def build_summary(results: list[ProcessingResult]) -> str:
    header = [
        "# Image Processing Summary",
        "",
        "| File | Before (bytes) | After (bytes) | Delta (bytes) | Dimensions |",
        "| --- | ---: | ---: | ---: | --- |",
    ]

    body = []
    total_before = 0
    total_after = 0
    for result in results:
        delta = result.processed_size_bytes - result.original_size_bytes
        total_before += result.original_size_bytes
        total_after += result.processed_size_bytes
        body.append(
            "| "
            f"{result.input_path.name} | "
            f"{result.original_size_bytes} | "
            f"{result.processed_size_bytes} | "
            f"{delta:+d} | "
            f"{result.width}x{result.height} |"
        )

    total_delta = total_after - total_before
    footer = [
        f"| Total | {total_before} | {total_after} | {total_delta:+d} | - |",
        "",
        f"Processed {len(results)} image(s).",
    ]
    return "\n".join(header + body + footer) + "\n"


def process_directory(
    input_dir: Path,
    output_dir: Path,
    summary_path: Path,
    fade_strength: float = 0.55,
) -> list[ProcessingResult]:
    input_files = list(iter_image_files(input_dir))
    if not input_files:
        raise ValueError(f"No supported images were found in {input_dir}.")

    results = [
        process_image(path, output_dir=output_dir, fade_strength=fade_strength)
        for path in input_files
    ]
    summary_path.parent.mkdir(parents=True, exist_ok=True)
    summary_path.write_text(build_summary(results), encoding="utf-8")
    return results


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Apply a simple fade effect to image fixtures and report size changes."
    )
    parser.add_argument("--input-dir", type=Path, required=True, help="Directory of input images.")
    parser.add_argument(
        "--output-dir",
        type=Path,
        required=True,
        help="Directory where processed PNG images will be written.",
    )
    parser.add_argument(
        "--summary",
        type=Path,
        default=Path("artifacts/image-summary.md"),
        help="Path where the markdown summary report will be written.",
    )
    parser.add_argument(
        "--fade-strength",
        type=float,
        default=0.55,
        help="Fade strength between 0 and 1.",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    process_directory(
        input_dir=args.input_dir,
        output_dir=args.output_dir,
        summary_path=args.summary,
        fade_strength=args.fade_strength,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
