#!/usr/bin/env python3
"""Scene composer: turn a still PNG into a short H.264 MP4 with camera motion.

Standalone CLI tool. Reads a final PNG (e.g. a ComfyUI render), applies a
camera-motion preset (slow zoom / dramatic zoom / side pan / dolly in / dolly
out) via OpenCV, optionally mixes in a music bed, and muxes the result to MP4
with ffmpeg so the file stays under the Telegram 10 MB limit.

This tool does NOT call ComfyUI. It only reads image files and writes MP4s.

Critical conventions replicated from the validated ``camera_motion_stable.py``:
  * OpenCV loads / stores frames as **BGR**. We hand raw BGR bytes to ffmpeg as
    ``-pix_fmt bgr24`` so colours are never swapped (the equivalent of the
    ``frame[:, :, ::-1]`` fix needed when writing via ``cv2.VideoWriter``).
  * **Center-based crop** performed in the source image coordinate space to
    avoid the classic +/-1 px drift.
  * Aspect ratio handled by a *cover* fit + centre crop (e.g. 1280x801 ->
    1280x800), tolerating non-square source pixels.
  * Resizing uses ``cv2.INTER_LINEAR`` (LANCZOS is not available in OpenCV's
    Python bindings).

Usage examples::

    python scene_composer.py input.png --motion slow_zoom --duration 10 --output out.mp4
    python scene_composer.py input.png --motion side_pan --duration 8 --music bed.mp3 --output reel.mp4
    python scene_composer.py input.png --motion dramatic_zoom --resolution 1280 800 --output trailer.mp4
"""

from __future__ import annotations

import argparse
import logging
import math
import shutil
import subprocess
import sys
import tempfile
from dataclasses import asdict, dataclass, field
from pathlib import Path

import cv2
import numpy as np

try:  # pyyaml is optional; sensible built-in presets are used as a fallback.
    import yaml
except ImportError:  # pragma: no cover - exercised only when pyyaml missing
    yaml = None

LOGGER = logging.getLogger("scene_composer")

DEFAULT_MOTION = "slow_zoom"
DEFAULT_DURATION_S = 10.0
DEFAULT_FPS = 24
DEFAULT_RESOLUTION: tuple[int, int] = (1280, 800)

# Telegram single-file ceiling and the internal target we tune toward.
TELEGRAM_LIMIT_MB = 10.0
TARGET_SIZE_MB = 8.0

# CRF ladder used by the auto-tuner (higher CRF -> smaller file, lower quality).
CRF_LADDER: tuple[int, ...] = (23, 26, 30)
DEFAULT_MAX_ATTEMPTS = 3  # encode attempts per resolution before downscaling.
MAX_DOWNSCALE_STEPS = 1  # only halve resolution once (4x -> 2x territory).

# Fallback presets, mirrored in config/motion_presets.yaml.
_BUILTIN_PRESETS: dict[str, dict[str, float]] = {
    "slow_zoom": {"zoom_start": 1.0, "zoom_end": 1.15, "pan_x": 0, "pan_y": 0},
    "dramatic_zoom": {"zoom_start": 1.0, "zoom_end": 1.4, "pan_x": 0, "pan_y": 0},
    "side_pan": {"zoom_start": 1.0, "zoom_end": 1.0, "pan_x": 40, "pan_y": 0},
    "dolly_in": {"zoom_start": 1.0, "zoom_end": 1.2, "pan_x": 0, "pan_y": 10},
    "dolly_out": {"zoom_start": 1.3, "zoom_end": 1.0, "pan_x": 0, "pan_y": 0},
}

_PRESETS_PATH = Path(__file__).resolve().parent.parent / "config" / "motion_presets.yaml"


def load_presets(path: Path | None = None) -> dict[str, dict[str, float]]:
    """Load motion presets from YAML, falling back to built-in definitions."""
    presets_path = path or _PRESETS_PATH
    if yaml is not None and presets_path.is_file():
        try:
            data = yaml.safe_load(presets_path.read_text(encoding="utf-8")) or {}
            presets = data.get("presets", {})
            merged: dict[str, dict[str, float]] = {}
            for name, cfg in presets.items():
                merged[name] = {
                    "zoom_start": float(cfg.get("zoom_start", 1.0)),
                    "zoom_end": float(cfg.get("zoom_end", 1.0)),
                    "pan_x": float(cfg.get("pan_x", 0)),
                    "pan_y": float(cfg.get("pan_y", 0)),
                }
            if merged:
                return merged
        except (OSError, ValueError, TypeError) as exc:  # pragma: no cover
            LOGGER.warning("Falling back to built-in presets (%s)", exc)
    return {name: dict(cfg) for name, cfg in _BUILTIN_PRESETS.items()}


@dataclass
class ComposeResult:
    """Structured result returned by :meth:`SceneComposer.compose`."""

    output_path: Path
    duration_s: float
    frames: int
    file_size_mb: float
    crf_used: int
    attempts: int
    resolution: tuple[int, int]
    encoding_warnings: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        data = asdict(self)
        data["output_path"] = str(self.output_path)
        return data


def ffmpeg_binary() -> str:
    """Return an available ffmpeg executable (PATH first, then imageio-ffmpeg)."""
    exe = shutil.which("ffmpeg")
    if exe:
        return exe
    try:  # optional dependency; only used as a fallback source of the binary.
        import imageio_ffmpeg  # type: ignore

        return imageio_ffmpeg.get_ffmpeg_exe()
    except Exception as exc:  # pragma: no cover - environment dependent
        raise RuntimeError(
            "ffmpeg not found on PATH and imageio-ffmpeg is unavailable. "
            "Install ffmpeg or `pip install imageio-ffmpeg`."
        ) from exc


class SceneComposer:
    """Compose a short camera-motion video clip from a single still image."""

    def __init__(
        self,
        input_path: Path,
        output_path: Path,
        *,
        motion: str = DEFAULT_MOTION,
        duration_s: float = DEFAULT_DURATION_S,
        fps: int = DEFAULT_FPS,
        resolution: tuple[int, int] = DEFAULT_RESOLUTION,
        music: Path | None = None,
        presets: dict[str, dict[str, float]] | None = None,
        max_attempts: int = DEFAULT_MAX_ATTEMPTS,
    ) -> None:
        self.input_path = Path(input_path)
        self.output_path = Path(output_path)
        self.motion = motion
        self.duration_s = float(duration_s)
        self.fps = int(fps)
        self.resolution = (int(resolution[0]), int(resolution[1]))
        self.music = Path(music) if music is not None else None
        self.presets = presets if presets is not None else load_presets()
        self.max_attempts = max(1, int(max_attempts))

        if self.motion not in self.presets:
            available = ", ".join(sorted(self.presets))
            raise ValueError(f"Unknown motion '{self.motion}'. Available: {available}")
        if self.duration_s <= 0:
            raise ValueError("duration_s must be > 0")
        if self.fps <= 0:
            raise ValueError("fps must be > 0")
        if self.resolution[0] <= 0 or self.resolution[1] <= 0:
            raise ValueError("resolution values must be > 0")

    # -- public API ---------------------------------------------------------

    @property
    def frame_count(self) -> int:
        """Total number of frames for the requested duration (>= 1)."""
        return max(1, round(self.duration_s * self.fps))

    def compose(self) -> ComposeResult:
        """Render the clip and encode it, auto-tuning to fit the size budget."""
        image = self._load_image()
        warnings: list[str] = []
        resolution = self.resolution
        crf_idx = 0
        attempts = 0
        downscales = 0
        last_path: Path | None = None
        last_size = math.inf
        last_crf = CRF_LADDER[0]

        frames = self._render_frames(image, resolution)

        while True:
            crf = CRF_LADDER[min(crf_idx, len(CRF_LADDER) - 1)]
            attempts += 1
            last_path = self._encode(frames, self.music, crf=crf, resolution=resolution)
            last_size = last_path.stat().st_size / 1_000_000
            last_crf = crf
            LOGGER.info(
                "Encoded attempt %d: %dx%d crf=%d -> %.2f MB",
                attempts,
                resolution[0],
                resolution[1],
                crf,
                last_size,
            )
            if last_size <= TARGET_SIZE_MB:
                break

            attempts_this_res = crf_idx + 1
            if crf_idx < len(CRF_LADDER) - 1 and attempts_this_res < self.max_attempts:
                crf_idx += 1
                warnings.append(
                    f"Output {last_size:.1f} MB > {TARGET_SIZE_MB:.0f} MB; "
                    f"bumping CRF to {CRF_LADDER[crf_idx]}."
                )
                continue

            # CRF ladder exhausted for this resolution.
            if last_size <= TELEGRAM_LIMIT_MB:
                break  # acceptable: under the hard 10 MB limit.
            if downscales < MAX_DOWNSCALE_STEPS:
                downscales += 1
                resolution = self._halve_resolution(resolution)
                frames = self._render_frames(image, resolution)
                crf_idx = 0
                warnings.append(
                    f"Output {last_size:.1f} MB > {TELEGRAM_LIMIT_MB:.0f} MB; "
                    f"downscaling to {resolution[0]}x{resolution[1]} and retrying from CRF 23."
                )
                continue
            break  # give up: return the smallest we managed.

        assert last_path is not None
        if last_path != self.output_path:
            self.output_path.parent.mkdir(parents=True, exist_ok=True)
            shutil.move(str(last_path), str(self.output_path))

        if last_size > TELEGRAM_LIMIT_MB:
            warnings.append(
                f"Final size {last_size:.1f} MB still exceeds the "
                f"{TELEGRAM_LIMIT_MB:.0f} MB Telegram limit after {attempts} attempts."
            )

        return ComposeResult(
            output_path=self.output_path,
            duration_s=self.frame_count / self.fps,
            frames=self.frame_count,
            file_size_mb=round(self.output_path.stat().st_size / 1_000_000, 3),
            crf_used=last_crf,
            attempts=attempts,
            resolution=resolution,
            encoding_warnings=warnings,
        )

    # -- motion -------------------------------------------------------------

    def _apply_motion(
        self,
        frame: np.ndarray,
        motion_type: str,
        t: float,
        total_t: float,
    ) -> np.ndarray:
        """Return a single output-resolution frame for time ``t``.

        ``frame`` is the prepared *base* image (already cover-fitted with pan
        headroom). A centre-based crop window is computed in the base image's
        own coordinate space and scaled to the output resolution with
        ``cv2.INTER_LINEAR`` -- no +/-1 px drift.
        """
        preset = self.presets[motion_type]
        progress = 0.0 if total_t <= 0 else min(max(t / total_t, 0.0), 1.0)

        zoom = preset["zoom_start"] + (preset["zoom_end"] - preset["zoom_start"]) * progress
        zoom = max(zoom, 1.0)

        out_w, out_h = self.resolution
        base_h, base_w = frame.shape[:2]

        # Crop window size in base-image pixels for this zoom level.
        crop_w = out_w / zoom
        crop_h = out_h / zoom

        # Centred pan: travel from -pan/2 to +pan/2 across the clip.
        offset_x = preset["pan_x"] * (progress - 0.5)
        offset_y = preset["pan_y"] * (progress - 0.5)

        center_x = base_w / 2.0 + offset_x
        center_y = base_h / 2.0 + offset_y

        # Clamp the centre so the crop window stays fully inside the base image.
        half_w = crop_w / 2.0
        half_h = crop_h / 2.0
        center_x = min(max(center_x, half_w), base_w - half_w)
        center_y = min(max(center_y, half_h), base_h - half_h)

        x0 = int(round(center_x - half_w))
        y0 = int(round(center_y - half_h))
        x1 = int(round(center_x + half_w))
        y1 = int(round(center_y + half_h))

        x0 = max(0, x0)
        y0 = max(0, y0)
        x1 = min(base_w, max(x1, x0 + 1))
        y1 = min(base_h, max(y1, y0 + 1))

        crop = frame[y0:y1, x0:x1]
        return cv2.resize(crop, (out_w, out_h), interpolation=cv2.INTER_LINEAR)

    # -- internals ----------------------------------------------------------

    def _load_image(self) -> np.ndarray:
        """Read the input image as a 3-channel BGR array."""
        if not self.input_path.is_file():
            raise FileNotFoundError(f"Input image not found: {self.input_path}")
        image = cv2.imread(str(self.input_path), cv2.IMREAD_COLOR)
        if image is None:
            raise ValueError(f"Could not decode image: {self.input_path}")
        return image

    def _prepare_base(self, image: np.ndarray, resolution: tuple[int, int]) -> np.ndarray:
        """Cover-fit ``image`` to the target aspect with pan/zoom headroom.

        Portrait or landscape sources are scaled to *cover* the base canvas and
        centre-cropped, so the output always fills the frame with no letterbox.
        Extra margin is added so panning never reveals the image edge.
        """
        out_w, out_h = resolution
        preset = self.presets[self.motion]
        # Headroom so the crop window + pan never leaves the base image.
        margin_x = int(math.ceil(abs(preset["pan_x"]))) + 2
        margin_y = int(math.ceil(abs(preset["pan_y"]))) + 2
        base_w = out_w + 2 * margin_x
        base_h = out_h + 2 * margin_y

        src_h, src_w = image.shape[:2]
        scale = max(base_w / src_w, base_h / src_h)
        new_w = max(base_w, int(math.ceil(src_w * scale)))
        new_h = max(base_h, int(math.ceil(src_h * scale)))
        resized = cv2.resize(image, (new_w, new_h), interpolation=cv2.INTER_LINEAR)

        # Centre crop to the base canvas (handles non-square source pixels).
        x0 = (new_w - base_w) // 2
        y0 = (new_h - base_h) // 2
        return resized[y0 : y0 + base_h, x0 : x0 + base_w]

    def _render_frames(
        self, image: np.ndarray, resolution: tuple[int, int]
    ) -> list[np.ndarray]:
        """Render every output frame into an in-memory list (BGR)."""
        # Temporarily point self.resolution at the working resolution so
        # _apply_motion produces frames at the right size during downscale.
        original_resolution = self.resolution
        self.resolution = resolution
        try:
            base = self._prepare_base(image, resolution)
            count = self.frame_count
            total_t = self.duration_s
            frames: list[np.ndarray] = []
            for i in range(count):
                # Sample time evenly across [0, duration].
                t = 0.0 if count == 1 else (i / (count - 1)) * total_t
                frames.append(
                    np.ascontiguousarray(self._apply_motion(base, self.motion, t, total_t))
                )
            return frames
        finally:
            self.resolution = original_resolution

    def _encode(
        self,
        frames: list[np.ndarray],
        music: Path | None,
        *,
        crf: int = 23,
        resolution: tuple[int, int] | None = None,
    ) -> Path:
        """Pipe raw BGR frames to ffmpeg and mux an H.264 MP4."""
        if not frames:
            raise ValueError("No frames to encode")
        width, height = resolution or self.resolution
        ffmpeg = ffmpeg_binary()

        tmp = tempfile.NamedTemporaryFile(suffix=".mp4", delete=False)
        tmp_path = Path(tmp.name)
        tmp.close()

        cmd = [
            ffmpeg,
            "-y",
            "-f",
            "rawvideo",
            "-pix_fmt",
            "bgr24",  # frames are BGR from OpenCV: no channel swap needed.
            "-s",
            f"{width}x{height}",
            "-r",
            str(self.fps),
            "-i",
            "pipe:0",
        ]
        if music is not None:
            cmd += ["-i", str(music)]
        cmd += [
            "-c:v",
            "libx264",
            "-preset",
            "medium",
            "-crf",
            str(crf),
            "-pix_fmt",
            "yuv420p",
            "-movflags",
            "+faststart",
        ]
        if music is not None:
            cmd += [
                "-c:a",
                "aac",
                "-b:a",
                "128k",
                "-ar",
                "44100",
                "-af",
                "volume=-12dB",  # mix the music bed in at -12 dB.
                "-shortest",
            ]
        else:
            cmd += ["-an"]
        cmd.append(str(tmp_path))

        payload = b"".join(frame.tobytes() for frame in frames)
        LOGGER.debug("ffmpeg cmd: %s", " ".join(cmd))
        proc = subprocess.run(cmd, input=payload, capture_output=True)
        if proc.returncode != 0:
            tmp_path.unlink(missing_ok=True)
            stderr = proc.stderr.decode("utf-8", errors="replace").strip()
            raise RuntimeError(f"ffmpeg failed (crf={crf}):\n{stderr}")
        return tmp_path

    @staticmethod
    def _halve_resolution(resolution: tuple[int, int]) -> tuple[int, int]:
        """Halve a resolution, keeping both dimensions even (>= 2) for H.264."""
        w = max(2, (resolution[0] // 2) // 2 * 2)
        h = max(2, (resolution[1] // 2) // 2 * 2)
        return (w, h)


# --------------------------------------------------------------------------
# CLI
# --------------------------------------------------------------------------

def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Turn a still PNG into a short camera-motion MP4 (Telegram-ready).",
    )
    parser.add_argument("input", type=Path, help="Input PNG (or other image) path.")
    parser.add_argument(
        "--motion",
        default=DEFAULT_MOTION,
        help=f"Motion preset (default: {DEFAULT_MOTION}).",
    )
    parser.add_argument(
        "--duration",
        type=float,
        default=DEFAULT_DURATION_S,
        dest="duration",
        help=f"Clip duration in seconds (default: {DEFAULT_DURATION_S}).",
    )
    parser.add_argument(
        "--fps", type=int, default=DEFAULT_FPS, help=f"Frames per second (default: {DEFAULT_FPS})."
    )
    parser.add_argument(
        "--resolution",
        type=int,
        nargs=2,
        metavar=("WIDTH", "HEIGHT"),
        default=list(DEFAULT_RESOLUTION),
        help=f"Output resolution (default: {DEFAULT_RESOLUTION[0]} {DEFAULT_RESOLUTION[1]}).",
    )
    parser.add_argument(
        "--music",
        type=Path,
        default=None,
        help="Optional music bed to mix in at -12 dB. Missing files are ignored.",
    )
    parser.add_argument(
        "--output",
        "-o",
        type=Path,
        default=None,
        help="Output MP4 path (default: <input stem>_<motion>.mp4 next to input).",
    )
    parser.add_argument("--verbose", "-v", action="store_true", help="Verbose logging.")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s | %(levelname)s | %(message)s",
    )

    output = args.output or args.input.with_name(f"{args.input.stem}_{args.motion}.mp4")

    music: Path | None = args.music
    if music is not None and not music.is_file():
        LOGGER.warning("Music file not found, producing a silent video: %s", music)
        music = None

    try:
        composer = SceneComposer(
            args.input,
            output,
            motion=args.motion,
            duration_s=args.duration,
            fps=args.fps,
            resolution=(args.resolution[0], args.resolution[1]),
            music=music,
        )
        result = composer.compose()
    except (FileNotFoundError, ValueError, RuntimeError) as exc:
        LOGGER.error("%s", exc)
        return 1

    LOGGER.info(
        "Wrote %s (%.2f MB, %d frames, %.1fs, %dx%d, crf=%d, attempts=%d)",
        result.output_path,
        result.file_size_mb,
        result.frames,
        result.duration_s,
        result.resolution[0],
        result.resolution[1],
        result.crf_used,
        result.attempts,
    )
    for warning in result.encoding_warnings:
        LOGGER.warning("%s", warning)
    return 0


if __name__ == "__main__":
    sys.exit(main())
