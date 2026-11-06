# -*- coding: utf-8 -*-
"""Lock a clip's face through ComfyUI ReActor, frame by frame.

Recommended production order: FreeVideo -> SeedVR2 upscale -> face_lock -> RIFE.
Running after SeedVR2 costs more I/O and detection time at the upscaled resolution,
but prevents the diffusion upscaler from redrawing the transplanted identity. Running
before RIFE avoids swapping twice as many frames and lets RIFE interpolate an already
consistent face instead of inventing in-between identity artifacts. Omit ``--fps`` to
keep the input frame rate; use ``--fps 48`` only when deliberately overriding it.

Usage:
  python scripts/face_lock.py --video clip.mp4 --source photo.jpg --out out.mp4
      [--fps 48] [--roi x,y,w,h] [--skip-threshold N] [--max-frames N]
"""
from __future__ import annotations

import argparse
import logging
import os
import subprocess
import sys
import tempfile
from fractions import Fraction
from pathlib import Path
from typing import Any

from PIL import Image, ImageChops, ImageStat

try:
    from .face_swap_clip import BASE, DEFAULT_RESTORE, ComfyUIClient, ComfyUIError
except ImportError:  # Direct execution puts scripts/ on sys.path.
    from face_swap_clip import BASE, DEFAULT_RESTORE, ComfyUIClient, ComfyUIError

LOGGER = logging.getLogger("face_lock")


def parse_roi(value: str | None) -> tuple[int, int, int, int] | None:
    """Parse an ``x,y,w,h`` face rectangle without touching the filesystem."""
    if value is None:
        return None
    try:
        parts = tuple(int(part.strip()) for part in value.split(","))
    except ValueError as exc:
        raise ValueError("ROI must contain four integers: x,y,w,h") from exc
    if len(parts) != 4:
        raise ValueError("ROI must contain four integers: x,y,w,h")
    x, y, width, height = parts
    if x < 0 or y < 0 or width <= 0 or height <= 0:
        raise ValueError("ROI requires x,y >= 0 and w,h > 0")
    return parts


def _crop_face(image: Image.Image, roi: tuple[int, int, int, int]) -> Image.Image:
    x, y, width, height = roi
    box = (x, y, min(x + width, image.width), min(y + height, image.height))
    if box[0] >= box[2] or box[1] >= box[3]:
        raise ValueError("ROI lies outside the image")
    return image.crop(box)


def face_difference(
    first: str | Path,
    current: str | Path,
    roi: tuple[int, int, int, int],
) -> float:
    """Return the 0-255 mean absolute RGB difference inside the face ROI."""
    with Image.open(first) as first_image, Image.open(current) as current_image:
        if first_image.size != current_image.size:
            raise ValueError("Frame dimensions changed during the clip")
        first_face = _crop_face(first_image.convert("RGB"), roi)
        current_face = _crop_face(current_image.convert("RGB"), roi)
        means = ImageStat.Stat(ImageChops.difference(first_face, current_face)).mean
        return sum(means) / len(means)


def should_skip(difference: float, threshold: float) -> bool:
    """A zero threshold disables reuse; otherwise equality is close enough."""
    return threshold > 0 and difference <= threshold


def reuse_swapped_roi(
    swapped: str | Path,
    current: str | Path,
    output: str | Path,
    roi: tuple[int, int, int, int],
) -> None:
    """Reuse only the stable face, retaining motion elsewhere in the frame."""
    with Image.open(swapped) as swapped_image, Image.open(current) as current_image:
        if swapped_image.size != current_image.size:
            raise ValueError("Frame dimensions changed during the clip")
        face = _crop_face(swapped_image.convert("RGB"), roi)
        result = current_image.convert("RGB")
        result.paste(face, (roi[0], roi[1]))
        result.save(output, format="PNG")


FRAME_PATTERN = "frame_%06d.png"


def build_extract_command(
    video: str | Path,
    frame_dir: str | Path,
    max_frames: int | None = None,
) -> list[str]:
    """Build a lossless, source-rate frame extraction command."""
    command = [
        "ffmpeg", "-y", "-v", "error", "-i", str(video),
        "-fps_mode", "passthrough", "-start_number", "0",
    ]
    if max_frames is not None:
        command.extend(["-frames:v", str(max_frames)])
    command.append(str(Path(frame_dir) / FRAME_PATTERN))
    return command


def build_encode_command(
    frame_dir: str | Path,
    video: str | Path,
    output: str | Path,
    fps: float,
    frames: int | None = None,
) -> list[str]:
    """Build the H.264 assembly command and stream-copy the original audio.

    The frame count is pinned with -frames:v. With -shortest the length came
    from the audio stream instead, and an AAC stream is never exactly as long
    as the video: the 7.29 s clip lost four frames at the end and the video
    finished before the music did.
    """
    command = [
        "ffmpeg", "-y", "-v", "error",
        "-framerate", format(fps, ".12g"), "-start_number", "0",
        "-i", str(Path(frame_dir) / FRAME_PATTERN),
        "-i", str(video),
        "-map", "0:v:0", "-map", "1:a:0?",
    ]
    if frames is not None:
        command.extend(["-frames:v", str(int(frames))])
    command += [
        "-c:v", "libx264", "-preset", "medium", "-crf", "17",
        "-pix_fmt", "yuv420p", "-c:a", "copy",
        "-movflags", "+faststart", str(output),
    ]
    return command


def process_frames(
    frames: list[Path],
    source: str | Path,
    output_dir: str | Path,
    client: Any,
    *,
    roi: tuple[int, int, int, int] | None = None,
    skip_threshold: float = 0.0,
    restore_model: str = DEFAULT_RESTORE,
    frame_timeout: float = 240,
    progress_every: int = 10,
) -> dict[str, int]:
    """Swap every changed frame and reuse only the face ROI for stable frames."""
    if skip_threshold < 0:
        raise ValueError("--skip-threshold must be >= 0")
    if skip_threshold > 0 and roi is None:
        raise ValueError("--skip-threshold requires --roi so only the face is compared")
    if progress_every <= 0:
        raise ValueError("--progress-every must be > 0")
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    stats = {"total": len(frames), "swapped": 0, "reused": 0}
    first_swapped: Path | None = None

    for index, frame in enumerate(frames):
        destination = output_dir / frame.name
        difference = (
            face_difference(frames[0], frame, roi)
            if index > 0 and roi is not None and skip_threshold > 0
            else None
        )
        if (
            difference is not None
            and first_swapped is not None
            and should_skip(difference, skip_threshold)
        ):
            reuse_swapped_roi(first_swapped, frame, destination, roi)  # type: ignore[arg-type]
            stats["reused"] += 1
        else:
            client.swap_one(
                frame,
                source,
                destination,
                restore_model=restore_model,
                timeout=frame_timeout,
                tag=f"{index:06d}",
            )
            stats["swapped"] += 1
            if first_swapped is None:
                first_swapped = destination
        if index == 0 or (index + 1) % progress_every == 0 or index + 1 == len(frames):
            LOGGER.info(
                "frame %d/%d: swapped=%d reused=%d",
                index + 1,
                len(frames),
                stats["swapped"],
                stats["reused"],
            )
    return stats


class FaceLockError(RuntimeError):
    """Invalid input or a failed ffmpeg/ffprobe stage."""


def _run(command: list[str], *, timeout: int) -> None:
    try:
        subprocess.run(command, check=True, timeout=timeout)
    except FileNotFoundError as exc:
        raise FaceLockError(f"Required executable is not on PATH: {command[0]}") from exc
    except subprocess.TimeoutExpired as exc:
        raise FaceLockError(f"{command[0]} timed out after {timeout}s") from exc
    except subprocess.CalledProcessError as exc:
        raise FaceLockError(f"{command[0]} failed with exit code {exc.returncode}") from exc


def extract_frames(
    video: str | Path, frame_dir: str | Path, max_frames: int | None = None
) -> list[Path]:
    """Extract a contiguous PNG sequence, optionally capped for a short smoke run."""
    frame_dir = Path(frame_dir)
    frame_dir.mkdir(parents=True, exist_ok=True)
    _run(build_extract_command(video, frame_dir, max_frames), timeout=600)
    frames = sorted(frame_dir.glob("frame_*.png"))
    if not frames:
        raise FaceLockError("ffmpeg extracted no frames from the input video")
    return frames


def parse_frame_rate(value: str) -> float:
    """Parse ffprobe's rational frame rate without losing NTSC precision."""
    try:
        fps = float(Fraction(value.strip()))
    except (ValueError, ZeroDivisionError) as exc:
        raise ValueError(f"Invalid video frame rate: {value!r}") from exc
    if fps <= 0:
        raise ValueError(f"Invalid video frame rate: {value!r}")
    return fps


def probe_fps(video: str | Path) -> float:
    """Read the source video's average frame rate."""
    command = [
        "ffprobe", "-v", "error", "-select_streams", "v:0",
        "-show_entries", "stream=avg_frame_rate", "-of",
        "default=nokey=1:noprint_wrappers=1", str(video),
    ]
    try:
        result = subprocess.run(
            command, check=True, capture_output=True, text=True, timeout=60
        )
    except FileNotFoundError as exc:
        raise FaceLockError("Required executable is not on PATH: ffprobe") from exc
    except (subprocess.TimeoutExpired, subprocess.CalledProcessError) as exc:
        raise FaceLockError("ffprobe could not read the input frame rate") from exc
    try:
        return parse_frame_rate(result.stdout)
    except ValueError as exc:
        raise FaceLockError(str(exc)) from exc


def encode_video(
    frame_dir: str | Path,
    video: str | Path,
    output: str | Path,
    fps: float,
    frames: int | None = None,
) -> None:
    """Encode all processed frames and retain audio without recompression."""
    output = Path(output)
    output.parent.mkdir(parents=True, exist_ok=True)
    _run(build_encode_command(frame_dir, video, output, fps, frames), timeout=1200)
    if not output.is_file() or output.stat().st_size == 0:
        raise FaceLockError("ffmpeg did not create a non-empty output video")


def run_face_lock(
    video: str | Path,
    source: str | Path,
    output: str | Path,
    *,
    fps: float | None = None,
    roi: tuple[int, int, int, int] | None = None,
    skip_threshold: float = 0.0,
    max_frames: int | None = None,
    frame_timeout: float = 240,
    progress_every: int = 10,
    restore_model: str = DEFAULT_RESTORE,
    comfyui_url: str = BASE,
    client: Any | None = None,
) -> dict[str, int]:
    """Run the complete extract -> ReActor -> H.264/audio-copy pipeline."""
    video, source, output = Path(video), Path(source), Path(output)
    if not video.is_file():
        raise FaceLockError(f"Input video does not exist: {video}")
    if not source.is_file():
        raise FaceLockError(f"Source photo does not exist: {source}")
    if fps is not None and fps <= 0:
        raise FaceLockError("--fps must be > 0")
    if max_frames is not None and max_frames <= 0:
        raise FaceLockError("--max-frames must be > 0")
    if frame_timeout <= 0:
        raise FaceLockError("--frame-timeout must be > 0")
    if video.resolve() == output.resolve():
        raise FaceLockError("--out must differ from --video")

    client = client or ComfyUIClient(comfyui_url)
    client.preflight(restore_model)
    with tempfile.TemporaryDirectory(prefix="face_lock_") as temporary:
        work = Path(temporary)
        frames = extract_frames(video, work / "input", max_frames)
        actual_fps = fps if fps is not None else probe_fps(video)
        LOGGER.info("processing %d frames at %.6g fps", len(frames), actual_fps)
        stats = process_frames(
            frames,
            source,
            work / "output",
            client,
            roi=roi,
            skip_threshold=skip_threshold,
            restore_model=restore_model,
            frame_timeout=frame_timeout,
            progress_every=progress_every,
        )
        encode_video(work / "output", video, output, actual_fps, len(frames))
    LOGGER.info("created %s: %s", output, stats)
    return stats


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--video", required=True, help="rendered clip to repair")
    parser.add_argument("--source", required=True, help="photo containing the reference face")
    parser.add_argument("--out", required=True, help="output MP4")
    parser.add_argument("--fps", type=float, help="output fps; default preserves the input")
    parser.add_argument("--roi", type=parse_roi, help="face rectangle x,y,w,h for skip heuristic")
    parser.add_argument(
        "--skip-threshold", type=float, default=0.0,
        help="reuse first swapped face ROI when mean abs difference is at most N (0=off)",
    )
    parser.add_argument("--max-frames", type=int, help="process only the first N frames")
    parser.add_argument("--frame-timeout", type=float, default=240, help="seconds per frame")
    parser.add_argument("--progress-every", type=int, default=10, help="log every N frames")
    parser.add_argument("--restore", default=DEFAULT_RESTORE, help="ReActor restore model")
    parser.add_argument(
        "--comfyui-url", default=os.environ.get("COMFYUI_URL", BASE),
        help="ComfyUI base URL (or COMFYUI_URL)",
    )
    return parser


def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    args = build_parser().parse_args()
    try:
        run_face_lock(
            args.video,
            args.source,
            args.out,
            fps=args.fps,
            roi=args.roi,
            skip_threshold=args.skip_threshold,
            max_frames=args.max_frames,
            frame_timeout=args.frame_timeout,
            progress_every=args.progress_every,
            restore_model=args.restore,
            comfyui_url=args.comfyui_url,
        )
    except (FaceLockError, ComfyUIError, ValueError, OSError) as exc:
        LOGGER.error("%s", exc)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
