#!/usr/bin/env python3
"""Thumbnail generator for furniture videos."""

from __future__ import annotations

import argparse
import datetime as dt
import logging
import math
import subprocess
import tempfile
from pathlib import Path
from typing import Iterable, Sequence

from PIL import Image, ImageDraw, ImageEnhance, ImageFilter, ImageFont, ImageOps, UnidentifiedImageError

DEFAULT_INPUT_DIR = Path("media/out/main")
DEFAULT_OUTPUT_DIR = Path("thumbnails")
DEFAULT_LOG_PATH = Path("memory/thumbnail_log.md")
DEFAULT_FRAME_COUNT = 12
DEFAULT_SIZES: tuple[tuple[int, int], ...] = ((1920, 1080), (1280, 720), (640, 360))
SUPPORTED_EXTENSIONS = {".mp4"}
LAPLACIAN_3X3 = ImageFilter.Kernel((3, 3), [-1, -1, -1, -1, 8, -1, -1, -1, -1], scale=1)
def setup_logger(verbose: bool = False) -> logging.Logger:
    """Build logger for console output."""
    logger = logging.getLogger("video_thumbnail_generator")
    if logger.handlers:
        return logger
    level = logging.DEBUG if verbose else logging.INFO
    logger.setLevel(level)
    handler = logging.StreamHandler()
    handler.setLevel(level)
    handler.setFormatter(logging.Formatter("%(asctime)s | %(levelname)s | %(message)s"))
    logger.addHandler(handler)
    return logger
def _run_command(command: Sequence[str], logger: logging.Logger) -> subprocess.CompletedProcess:
    """Run ffmpeg/ffprobe commands with error conversion."""
    logger.debug("CMD: %s", " ".join(command))
    try:
        return subprocess.run(command, check=True, capture_output=True, text=True)
    except FileNotFoundError as exc:
        raise RuntimeError(f"Missing executable: {command[0]}") from exc
    except subprocess.CalledProcessError as exc:
        raise RuntimeError((exc.stderr or str(exc)).strip()) from exc
def _probe_duration(video_path: Path, logger: logging.Logger) -> float:
    """Read media duration (seconds) from ffprobe."""
    cmd = [
        "ffprobe",
        "-v",
        "error",
        "-show_entries",
        "format=duration",
        "-of",
        "default=nokey=1:noprint_wrappers=1",
        str(video_path),
    ]
    output = _run_command(cmd, logger).stdout.strip()
    try:
        duration = float(output)
    except ValueError as exc:
        raise RuntimeError(f"Could not parse duration for {video_path.name}: {output!r}") from exc
    if duration <= 0:
        raise RuntimeError(f"Invalid duration for {video_path.name}: {duration}")
    return duration


def extract_frames(video_path: Path, frame_count: int, logger: logging.Logger) -> list[Path]:
    """Extract evenly sampled JPG frames from video into temp dir."""
    duration = _probe_duration(video_path, logger)
    start = max(0.0, duration * 0.1)
    end = max(start + 0.25, duration * 0.9)
    clip_span = max(end - start, 0.5)
    fps = max(frame_count / clip_span, 0.25)
    temp_dir = Path(tempfile.mkdtemp(prefix=f"{video_path.stem}-frames-"))
    pattern = temp_dir / "frame_%04d.jpg"
    cmd = [
        "ffmpeg",
        "-y",
        "-v",
        "error",
        "-ss",
        f"{start:.3f}",
        "-i",
        str(video_path),
        "-t",
        f"{clip_span:.3f}",
        "-vf",
        f"fps={fps:.5f}",
        "-frames:v",
        str(frame_count),
        str(pattern),
    ]
    _run_command(cmd, logger)
    frames = sorted(temp_dir.glob("frame_*.jpg"))
    if not frames:
        raise RuntimeError(f"No frames extracted from {video_path.name}")
    logger.info("Extracted %d frame(s) from %s", len(frames), video_path.name)
    return frames


def _variance(values: Iterable[int | float]) -> float:
    nums = [float(v) for v in values]
    if not nums:
        return 0.0
    mean = sum(nums) / len(nums)
    return sum((v - mean) ** 2 for v in nums) / len(nums)


def _laplacian_variance(image: Image.Image) -> float:
    """Return sharpness metric from Laplacian response variance."""
    gray = ImageOps.grayscale(image)
    return _variance(gray.filter(LAPLACIAN_3X3).getdata())


def _center_bias(index: int, total: int) -> float:
    """Prefer center timeline frames to avoid title/outro cards."""
    if total <= 1:
        return 1.0
    center = (total - 1) / 2
    normalized_distance = abs(index - center) / max(center, 1.0)
    return max(0.6, 1.0 - normalized_distance * 0.35)


def select_best_frame(frame_paths: Sequence[Path], logger: logging.Logger) -> Path:
    """Select frame with highest center-biased Laplacian sharpness."""
    if not frame_paths:
        raise ValueError("frame_paths cannot be empty")
    best_path: Path | None = None
    best_score = -math.inf
    total = len(frame_paths)
    for i, frame_path in enumerate(frame_paths):
        try:
            with Image.open(frame_path) as img:
                sharpness = _laplacian_variance(img)
        except (OSError, UnidentifiedImageError):
            logger.warning("Unreadable frame skipped: %s", frame_path.name)
            continue
        score = sharpness * _center_bias(i, total)
        logger.debug("%s sharpness=%.2f score=%.2f", frame_path.name, sharpness, score)
        if score > best_score:
            best_path = frame_path
            best_score = score
    if best_path is None:
        raise RuntimeError("Could not score any extracted frame.")
    logger.info("Selected frame: %s (%.2f)", best_path.name, best_score)
    return best_path


def _minimax_stretch(image: Image.Image, floor: int = 8, ceiling: int = 247) -> Image.Image:
    """Apply simple per-channel min-max stretch with clipping."""
    rgb = image.convert("RGB")
    stretched_channels: list[Image.Image] = []
    for channel in rgb.split():
        low, high = channel.getextrema()
        low = min(max(low, floor), 254)
        high = max(min(high, ceiling), low + 1)
        denom = max(high - low, 1)
        lut = [max(0, min(255, int((v - low) * 255 / denom))) for v in range(256)]
        stretched_channels.append(channel.point(lut))
    return Image.merge("RGB", tuple(stretched_channels))


def enhance_thumbnail(
    image: Image.Image,
    use_minimax: bool,
    saturation: float = 1.08,
    contrast: float = 1.10,
    sharpness: float = 1.12,
) -> Image.Image:
    """Enhance selected frame for thumbnail readability."""
    out = image.convert("RGB")
    if use_minimax:
        out = _minimax_stretch(out)
    out = ImageEnhance.Color(out).enhance(saturation)
    out = ImageEnhance.Contrast(out).enhance(contrast)
    out = ImageEnhance.Sharpness(out).enhance(sharpness)
    return out


def _fit_fill(image: Image.Image, size: tuple[int, int]) -> Image.Image:
    """Resize and crop to fill target resolution."""
    target_w, target_h = size
    src_w, src_h = image.size
    scale = max(target_w / src_w, target_h / src_h)
    resized = image.resize((int(src_w * scale), int(src_h * scale)), Image.Resampling.LANCZOS)
    left = (resized.width - target_w) // 2
    top = (resized.height - target_h) // 2
    return resized.crop((left, top, left + target_w, top + target_h))


def apply_branding(image: Image.Image, text: str, logger: logging.Logger) -> Image.Image:
    """Overlay brand text in lower-left with a translucent rounded backdrop."""
    branded = image.convert("RGB")
    width, height = branded.size
    font_size = max(18, width // 28)
    try:
        font = ImageFont.truetype("DejaVuSans-Bold.ttf", font_size)
    except OSError:
        logger.warning("Brand font not found, falling back to default.")
        font = ImageFont.load_default()
    draw = ImageDraw.Draw(branded)
    left, top, right, bottom = draw.textbbox((0, 0), text, font=font)
    text_w = right - left
    text_h = bottom - top
    pad_x = max(12, width // 80)
    pad_y = max(10, height // 80)
    x = pad_x
    y = height - text_h - (2 * pad_y)
    rect = (x - pad_x // 2, y - pad_y // 2, x + text_w + pad_x, y + text_h + pad_y)
    overlay = Image.new("RGBA", branded.size, (0, 0, 0, 0))
    od = ImageDraw.Draw(overlay)
    od.rounded_rectangle(rect, radius=max(8, font_size // 3), fill=(0, 0, 0, 140))
    od.text((x, y), text, font=font, fill=(255, 255, 255, 236))
    return Image.alpha_composite(branded.convert("RGBA"), overlay).convert("RGB")


def generate_all_sizes(
    image: Image.Image,
    base_name: str,
    output_dir: Path,
    sizes: Sequence[tuple[int, int]],
    logger: logging.Logger,
) -> list[Path]:
    """Create thumbnails in requested resolutions."""
    output_dir.mkdir(parents=True, exist_ok=True)
    generated: list[Path] = []
    for width, height in sizes:
        out_path = output_dir / f"{base_name}_{width}x{height}.jpg"
        _fit_fill(image, (width, height)).save(out_path, "JPEG", quality=92, optimize=True)
        generated.append(out_path)
        logger.info("Saved: %s", out_path)
    return generated


def _cleanup_frames(frames: Sequence[Path], logger: logging.Logger) -> None:
    """Delete temporary extracted frame files."""
    frame_dirs: set[Path] = set()
    for frame in frames:
        frame_dirs.add(frame.parent)
        if frame.exists():
            frame.unlink()
    for frame_dir in frame_dirs:
        try:
            frame_dir.rmdir()
        except OSError:
            logger.debug("Temp frame dir not empty: %s", frame_dir)


def _append_markdown_log(log_path: Path, lines: list[str]) -> None:
    """Append one batch section to markdown log."""
    log_path.parent.mkdir(parents=True, exist_ok=True)
    with log_path.open("a", encoding="utf-8") as handle:
        if log_path.exists() and log_path.stat().st_size > 0:
            handle.write("\n")
        handle.write("\n".join(lines))
        handle.write("\n")


def batch_process(
    input_dir: Path,
    output_dir: Path,
    branding_text: str,
    use_minimax: bool,
    frame_count: int,
    logger: logging.Logger,
    log_path: Path = DEFAULT_LOG_PATH,
) -> dict[str, list[Path]]:
    """Process all mp4 files in directory and export thumbnails."""
    input_dir = input_dir.resolve()
    if not input_dir.exists():
        raise FileNotFoundError(f"Input directory missing: {input_dir}")
    videos = sorted(p for p in input_dir.iterdir() if p.is_file() and p.suffix.lower() in SUPPORTED_EXTENSIONS)
    if not videos:
        raise FileNotFoundError(f"No mp4 videos found in {input_dir}")
    results: dict[str, list[Path]] = {}
    log_lines = [
        f"## Thumbnail Batch - {dt.datetime.now(dt.timezone.utc).isoformat()}",
        "",
        f"- Input directory: `{input_dir}`",
        f"- Output directory: `{output_dir.resolve()}`",
        f"- Video count: **{len(videos)}**",
        f"- MiniMax enhancement: **{'enabled' if use_minimax else 'disabled'}**",
        "",
        "| Video | Status | Outputs |",
        "|---|---|---|",
    ]
    for video in videos:
        frames: list[Path] = []
        try:
            logger.info("Processing: %s", video.name)
            frames = extract_frames(video, frame_count=frame_count, logger=logger)
            best = select_best_frame(frames, logger=logger)
            with Image.open(best) as selected:
                enhanced = enhance_thumbnail(selected, use_minimax=use_minimax)
                branded = apply_branding(enhanced, branding_text, logger=logger)
                outputs = generate_all_sizes(branded, video.stem, output_dir, DEFAULT_SIZES, logger)
            results[video.name] = outputs
            joined = ", ".join(path.name for path in outputs)
            log_lines.append(f"| `{video.name}` | ✅ Success | `{joined}` |")
        except Exception as exc:  # noqa: BLE001
            logger.exception("Failed: %s", video.name)
            log_lines.append(f"| `{video.name}` | ❌ Failed | `{exc}` |")
        finally:
            _cleanup_frames(frames, logger=logger)
    _append_markdown_log(log_path, log_lines)
    logger.info("Batch complete. Log updated at %s", log_path)
    return results


def parse_args() -> argparse.Namespace:
    """Parse CLI arguments."""
    parser = argparse.ArgumentParser(description="Generate furniture video thumbnails.")
    parser.add_argument("--input-dir", type=Path, default=DEFAULT_INPUT_DIR, help="Directory containing mp4 files.")
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR, help="Directory for thumbnail jpgs.")
    parser.add_argument("--brand-text", type=str, default="Furniture Collection", help="Brand text overlay.")
    parser.add_argument("--use-minimax", action="store_true", help="Enable optional MiniMax enhancement.")
    parser.add_argument("--frame-count", type=int, default=DEFAULT_FRAME_COUNT, help="Candidate frames per video.")
    parser.add_argument("--log-path", type=Path, default=DEFAULT_LOG_PATH, help="Markdown batch log path.")
    parser.add_argument("--verbose", action="store_true", help="Enable debug logging.")
    return parser.parse_args()


def main() -> int:
    """CLI entrypoint."""
    args = parse_args()
    logger = setup_logger(args.verbose)
    try:
        batch_process(
            input_dir=args.input_dir,
            output_dir=args.output_dir,
            branding_text=args.brand_text,
            use_minimax=args.use_minimax,
            frame_count=max(3, args.frame_count),
            logger=logger,
            log_path=args.log_path,
        )
    except Exception as exc:  # noqa: BLE001
        logger.error("Thumbnail generation failed: %s", exc)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
