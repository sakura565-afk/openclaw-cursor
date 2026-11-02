"""Reusable hi-res post-processing: SeedVR2 upscale, RIFE, source-audio mux."""

from __future__ import annotations

import argparse
import json
import os
import tempfile
from contextlib import contextmanager
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Callable, Sequence

from video_pipeline.assembler import interpolate as _interpolate
from video_pipeline.assembler import probe as _probe
from video_pipeline.config import UpscaleConfig
from video_pipeline.upscale import mux_audio_from_source as _mux_audio
from video_pipeline.upscale import upscale_video as _upscale_video

DEFAULT_COMFY_URL = "http://127.0.0.1:8188"


class HiresError(RuntimeError):
    """Raised when the hi-res chain cannot produce the requested learned upscale."""


def _seedvr2_upscale(
    src: Path,
    out: Path,
    width: int,
    height: int,
    comfy_url: str,
) -> str:
    return _upscale_video(
        src,
        out,
        width,
        height,
        UpscaleConfig(backend="seedvr2", comfyui_url=comfy_url),
    )


def _rife_interpolate(
    src: Path,
    out: Path,
    fps: float,
    comfy_url: str,
) -> Path:
    return _interpolate(src, out, fps, comfyui_url=comfy_url)


@dataclass
class HiresRunner:
    """Injectable media operations; defaults call the existing pipeline libraries."""

    probe: Callable[[Path], dict[str, Any]] = _probe
    upscale: Callable[[Path, Path, int, int, str], str] = _seedvr2_upscale
    interpolate: Callable[[Path, Path, float, str], Path] = _rife_interpolate
    mux: Callable[[Path, Path, Path], Path] = _mux_audio
    quality_gate: Callable[[Path], Any] | None = None


@dataclass(frozen=True)
class HiresResult:
    """Measured output metadata and the upscale backend that actually ran."""

    src: Path
    out: Path
    width: int
    height: int
    fps: float
    duration: float
    has_audio: bool
    upscale_backend: str
    quality: Any = None


def _derived_width(src_width: int, src_height: int, target_height: int) -> int:
    """Derive a 32-aligned width from source AR, without stretching the frame."""
    raw = target_height * src_width / src_height
    return max(32, int(round(raw / 32)) * 32)


@contextmanager
def _fallback_guard(strict: bool):
    """Set the existing library's loud-failure switch and restore the caller's env."""
    previous = os.environ.get("UPSCALE_NO_FALLBACK")
    if strict:
        os.environ["UPSCALE_NO_FALLBACK"] = "1"
    try:
        yield
    finally:
        if previous is None:
            os.environ.pop("UPSCALE_NO_FALLBACK", None)
        else:
            os.environ["UPSCALE_NO_FALLBACK"] = previous


def build_hires(
    src: str | Path,
    out: str | Path,
    *,
    height: int = 1620,
    width: int = 0,
    interpolate_fps: float = 48.0,
    comfy_url: str = DEFAULT_COMFY_URL,
    strict: bool = True,
    runner: HiresRunner | None = None,
) -> HiresResult:
    """Upscale first, interpolate second, then restore audio from ``src``."""
    src = Path(src)
    out = Path(out)
    if height <= 0:
        raise ValueError(f"height must be a positive target height, got {height}")
    runner = runner or HiresRunner()
    with _fallback_guard(strict):
        source_info = runner.probe(src)
        target_width = width or _derived_width(
            int(source_info["width"]), int(source_info["height"]), height
        )

        out.parent.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(prefix=f".{out.stem}_hires_", dir=out.parent) as tmp:
            tmp_path = Path(tmp)
            upscaled = tmp_path / "upscaled.mp4"
            interpolated = tmp_path / "interpolated.mp4"
            backend = runner.upscale(src, upscaled, target_width, height, comfy_url)
            if strict and backend != "seedvr2":
                raise HiresError(
                    f"upscale backend was {backend!r}, not 'seedvr2' in strict mode; "
                    "refusing a result without learned detail"
                )
            runner.interpolate(upscaled, interpolated, interpolate_fps, comfy_url)
            runner.mux(interpolated, src, out)

        final_info = runner.probe(out)
        quality_gate = getattr(runner, "quality_gate", None)
        quality = quality_gate(out) if quality_gate is not None else None
        return HiresResult(
            src=src,
            out=out,
            width=int(final_info["width"]),
            height=int(final_info["height"]),
            fps=float(final_info["fps"]),
            duration=float(final_info["duration"]),
            has_audio=bool(final_info["has_audio"]),
            upscale_backend=backend,
            quality=quality,
        )


def _build_cli_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Hi-res post chain: SeedVR2 upscale -> RIFE -> source audio mux."
    )
    parser.add_argument("--src", required=True, type=Path, help="Source 24 fps clip.")
    parser.add_argument("--out", required=True, type=Path, help="Destination mp4 path.")
    parser.add_argument("--height", type=int, default=1620, help="Target upscale height.")
    parser.add_argument(
        "--width", type=int, default=0, help="Target width; 0 derives it from source AR."
    )
    parser.add_argument("--fps", type=float, default=48.0, help="RIFE target fps.")
    parser.add_argument("--comfy-url", default=DEFAULT_COMFY_URL, help="PROD ComfyUI URL.")
    parser.add_argument(
        "--no-strict",
        action="store_true",
        help="Allow the existing upscale library's explicit Lanczos fallback.",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _build_cli_parser().parse_args(list(argv) if argv is not None else None)
    result = build_hires(
        args.src,
        args.out,
        height=args.height,
        width=args.width,
        interpolate_fps=args.fps,
        comfy_url=args.comfy_url,
        strict=not args.no_strict,
    )
    print(json.dumps(asdict(result), ensure_ascii=False, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
