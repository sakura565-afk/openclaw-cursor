#!/usr/bin/env python3
"""
Stable eased camera motion for furniture product shots.

Produces a PNG sequence and an MP4 with smooth motion (smoothstep easing) suitable
for benchmarking RIFE interpolation and SUPIR upscaling without SD generation noise.

Typical flow:
  python scripts/camera_motion_stable.py --input photo.jpg --out_dir scripts/data/camera_motion_stable_out
"""

from __future__ import annotations

import argparse
import math
import subprocess
import sys
from pathlib import Path
from typing import List

from PIL import Image, ImageEnhance


def smoothstep(t: float) -> float:
    t = max(0.0, min(1.0, t))
    return t * t * (3.0 - 2.0 * t)


def eased_t(t: float, curve: str) -> float:
    if curve == "linear":
        return max(0.0, min(1.0, t))
    return smoothstep(t)


def apply_rotate(img: Image.Image, t: float) -> Image.Image:
    angle = -5.0 + 10.0 * t
    return img.rotate(angle, resample=Image.Resampling.BICUBIC, expand=False)


def apply_zoom(img: Image.Image, t: float) -> Image.Image:
    w, h = img.size
    scale = 1.0 + 0.12 * t
    nw, nh = int(w / scale), int(h / scale)
    left = (w - nw) // 2
    top = (h - nh) // 2
    crop = img.crop((left, top, left + nw, top + nh))
    return crop.resize((w, h), Image.Resampling.LANCZOS)


def apply_pan(img: Image.Image, t: float) -> Image.Image:
    w, h = img.size
    shift = int((w * 0.10) * (t - 0.5) * 2.0)
    left = max(0, min(w // 8, w // 2 + shift - w // 2))
    right = min(w, left + w - w // 8)
    crop = img.crop((left, 0, right, h))
    return crop.resize((w, h), Image.Resampling.LANCZOS)


def render_sequence(
    input_path: Path,
    out_dir: Path,
    width: int,
    height: int,
    duration: float,
    fps: int,
    effect: str,
    curve: str,
    light_swivel: float,
) -> tuple[List[Path], Path]:
    img = Image.open(input_path).convert("RGB")
    img = img.resize((width, height), Image.Resampling.LANCZOS)
    total = max(2, int(round(duration * fps)))
    out_dir.mkdir(parents=True, exist_ok=True)
    paths: List[Path] = []

    for i in range(total):
        u = i / max(1, total - 1)
        t = eased_t(u, curve)
        if effect == "rotate":
            frame = apply_rotate(img, t)
        elif effect == "zoom":
            frame = apply_zoom(img, t)
        elif effect == "pan":
            frame = apply_pan(img, t)
        else:
            raise ValueError(f"Unknown effect: {effect}")

        if light_swivel > 0:
            b = 0.96 + light_swivel * math.sin(u * math.pi * 2.0)
            frame = ImageEnhance.Brightness(frame).enhance(b)

        p = out_dir / f"frame_{i:05d}.png"
        frame.save(p, format="PNG")
        paths.append(p)

    mp4_path = out_dir / "stable_motion.mp4"
    _ffmpeg_encode_frames(out_dir, mp4_path, fps, total)
    return paths, mp4_path


def _ffmpeg_encode_frames(frame_dir: Path, out_mp4: Path, fps: int, frame_count: int) -> None:
    pattern = str(frame_dir / "frame_%05d.png")
    cmd = [
        "ffmpeg",
        "-y",
        "-framerate",
        str(fps),
        "-i",
        pattern,
        "-frames:v",
        str(frame_count),
        "-pix_fmt",
        "yuv420p",
        "-c:v",
        "libx264",
        str(out_mp4),
    ]
    try:
        subprocess.run(cmd, check=True, capture_output=True, text=True)
    except subprocess.CalledProcessError as exc:
        raise RuntimeError(f"ffmpeg failed: {exc.stderr}") from exc


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Stable eased camera motion → PNG + MP4.")
    p.add_argument("--input", required=True, type=Path, help="Source furniture photo (RGB).")
    p.add_argument(
        "--out_dir",
        type=Path,
        default=Path("scripts/data/camera_motion_stable_out"),
        help="Output directory for frame_*.png and stable_motion.mp4.",
    )
    p.add_argument("--width", type=int, default=768)
    p.add_argument("--height", type=int, default=768)
    p.add_argument("--duration", type=float, default=2.0, help="Clip length in seconds.")
    p.add_argument("--fps", type=int, default=60, help="Output sequence FPS (reference for RIFE tests).")
    p.add_argument("--effect", choices=["pan", "zoom", "rotate"], default="pan")
    p.add_argument("--curve", choices=["linear", "smoothstep"], default="smoothstep")
    p.add_argument(
        "--light_swivel",
        type=float,
        default=0.04,
        help="0 disables; small value adds slow brightness swing (studio sweep).",
    )
    return p.parse_args()


def main() -> int:
    args = parse_args()
    if not args.input.exists():
        print(f"[ERROR] Input not found: {args.input}", file=sys.stderr)
        return 1
    try:
        _, mp4 = render_sequence(
            args.input,
            args.out_dir,
            args.width,
            args.height,
            args.duration,
            args.fps,
            args.effect,
            args.curve,
            args.light_swivel,
        )
    except Exception as exc:  # noqa: BLE001
        print(f"[ERROR] {exc}", file=sys.stderr)
        return 1
    print(f"[OK] Wrote {args.out_dir} and {mp4}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
