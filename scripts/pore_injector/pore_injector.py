# -*- coding: utf-8 -*-
"""Pore injector CLI: add PORES (structure) to skin, not noise (energy).

    python scripts/pore_injector/pore_injector.py \
        --video <in.mp4> --out <out.mp4> \
        --strength 0.05 --pit-density 30 --rim 0.35 --pore-sigma 1.4

Everything runs on the CPU. Nothing here touches ComfyUI or the GPU.

Design in one paragraph: the pore field is built ONCE for the whole clip from
a seed, as a sparse population of dark pits with a brighter rim rather than
as an isotropic noise carpet. The field is warped by optical flow so a pit
stays on the same patch of skin, and the AMOUNT that gets injected comes from
the current frame's own high-frequency detail, gated by the skin mask and cut
off below a threshold - so smooth areas get nothing and there is no constant
floor anywhere.
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import cv2
import numpy as np

if __package__ in (None, ""):  # pragma: no cover - direct script execution
    # The repository root, so that `scripts.pore_injector...` resolves.
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from scripts.pore_injector.field import build_pore_field
from scripts.pore_injector.inject import FlowTracker, farneback_flow, structure_map
from scripts.pore_injector.mask import fallback_skin_mask, load_external_mask
from scripts.pore_injector.metrics import RunMetrics

__all__ = ["main", "run_injection"]

DEFAULT_FPS = 24.0


def _find_ffmpeg() -> str | None:
    return shutil.which("ffmpeg")


def _probe_video(path: Path) -> dict:
    """Width, height and frame rate of the first video stream."""
    ffprobe = shutil.which("ffprobe")
    if ffprobe is None:
        raise RuntimeError("ffprobe is not available on PATH")
    result = subprocess.run(
        [ffprobe, "-v", "error", "-select_streams", "v:0",
         "-show_entries", "stream=width,height,r_frame_rate,nb_frames",
         "-of", "json", str(path)],
        capture_output=True, text=True, check=True, timeout=120,
    )
    data = json.loads(result.stdout)
    stream = data["streams"][0]
    num, _, den = stream.get("r_frame_rate", "24/1").partition("/")
    fps = float(num) / float(den or 1) if float(den or 1) else DEFAULT_FPS
    return {
        "width": int(stream["width"]),
        "height": int(stream["height"]),
        "fps": fps,
        "frames": int(stream.get("nb_frames") or 0),
    }


def _decode_frames(path: Path, width: int, height: int, max_frames: int = 0):
    """Decode frames as float32 RGB in [0, 1], one at a time."""
    capture = cv2.VideoCapture(str(path))
    if not capture.isOpened():
        raise RuntimeError(f"cannot open video: {path}")
    count = 0
    try:
        while True:
            if max_frames and count >= max_frames:
                break
            ok, bgr = capture.read()
            if not ok:
                break
            yield cv2.cvtColor(bgr, cv2.COLOR_BGR2RGB).astype(np.float32) / 255.0
            count += 1
    finally:
        capture.release()


def _write_video(
    frames,
    out_path: Path,
    fps: float,
    width: int,
    height: int,
    source: Path | None = None,
) -> None:
    """Encode the processed frames, keeping the source audio when present."""
    ffmpeg = _find_ffmpeg()
    if ffmpeg is None:
        raise RuntimeError("ffmpeg is not available on PATH")

    out_path.parent.mkdir(parents=True, exist_ok=True)

    command = [ffmpeg, "-y", "-v", "error",
               "-f", "rawvideo", "-pixel_format", "rgb24",
               "-video_size", f"{width}x{height}", "-framerate", f"{fps}",
               "-i", "-"]
    if source is not None:
        command += ["-i", str(source), "-map", "0:v:0", "-map", "1:a:0?"]
    else:
        command += ["-map", "0:v:0"]
    command += ["-c:v", "libx264", "-crf", "16", "-pix_fmt", "yuv420p"]
    if source is not None:
        # No -shortest: the length came from the audio stream, and an AAC
        # stream is never exactly as long as the video, so the picture was cut
        # a frame short and finished before the music did. The audio is copied
        # so it stays bit-identical to the source.
        command += ["-c:a", "copy"]
    command += [str(out_path)]

    process = subprocess.Popen(command, stdin=subprocess.PIPE, stderr=subprocess.PIPE)
    assert process.stdin is not None
    written = 0
    try:
        for frame in frames:
            data = (np.clip(frame, 0.0, 1.0) * 255.0).round().astype(np.uint8)
            process.stdin.write(data.tobytes())
            written += 1
        process.stdin.close()
    except BrokenPipeError:  # pragma: no cover - ffmpeg died early
        pass
    stderr = process.stderr.read().decode("utf-8", "replace") if process.stderr else ""
    code = process.wait()
    if code != 0:
        raise RuntimeError(f"ffmpeg failed ({code}): {stderr[:600]}")
    if written == 0:
        raise RuntimeError("no frames were produced")


def _rife_available() -> tuple[bool, str]:
    """Is there a usable RIFE path in this environment?

    RIFE in this repository runs through ComfyUI (a GPU node). This tool is
    CPU-only by design, so RIFE is attempted through ffmpeg's frame
    interpolation and is reported honestly when it is not available.
    """
    ffmpeg = _find_ffmpeg()
    if ffmpeg is None:
        return False, "ffmpeg is not on PATH"
    try:
        result = subprocess.run(
            [ffmpeg, "-hide_banner", "-filters"],
            capture_output=True, text=True, timeout=60,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        return False, f"cannot query ffmpeg filters: {exc}"
    for line in result.stdout.splitlines():
        if "rife" in line.lower():
            return True, "ffmpeg rife filter"
    return False, "no rife filter in this ffmpeg build (ComfyUI RIFE needs the GPU, which this tool does not use)"


def _run_rife(source: Path, target: Path) -> dict:
    """Interpolate 12->48 or 24->48, if a RIFE path exists.

    Returns a dict describing what happened; on failure the caller simply
    continues without it.
    """
    available, reason = _rife_available()
    info = {"requested": True, "available": available, "reason": reason}
    if not available:
        return info

    ffmpeg = _find_ffmpeg()
    target.parent.mkdir(parents=True, exist_ok=True)
    try:
        subprocess.run(
            [ffmpeg, "-y", "-v", "error", "-i", str(source),
             "-vf", "rife", "-r", "48", str(target)],
            check=True, timeout=3600,
        )
    except (subprocess.SubprocessError, OSError) as exc:
        info["error"] = str(exc)[:400]
        return info
    info["output"] = str(target)
    return info


def run_injection(
    video: Path,
    out: Path,
    strength: float = 0.07,
    pit_density: float = 260.0,
    rim: float = 0.45,
    pore_sigma: float = 1.1,
    threshold: float = 0.55,
    mask_path: Path | None = None,
    temporal_sigma: float = 3.0,
    field_temporal_sigma: float = 3.0,
    seed: int = 1234,
    two_octave: bool = True,
    max_frames: int = 0,
    use_flow: bool = True,
    detail_sigma: float = 1.2,
    structure_window: float = 3.0,
    structure_reference: str = "local",
    roi: tuple[float, float, float, float] | None = None,
    rife_out: Path | None = None,
    report_path: Path | None = None,
) -> dict:
    """Run the injector over a clip and return the report dict."""
    video = Path(video)
    out = Path(out)
    if not video.exists():
        raise FileNotFoundError(f"video not found: {video}")

    probe = _probe_video(video)
    width, height = probe["width"], probe["height"]
    fps = probe["fps"] or DEFAULT_FPS

    # The field is generated ONCE, for the whole clip, from the seed.
    field, field_stats = build_pore_field(
        (height, width),
        seed=seed,
        pit_density=pit_density,
        rim=rim,
        pore_sigma=pore_sigma,
        two_octave=two_octave,
    )

    external_mask = None
    mask_stats: dict = {}
    if mask_path is not None:
        external_mask, mask_stats = load_external_mask(mask_path, (height, width))
        mask_stats = dict(mask_stats)

    tracker = FlowTracker((height, width))
    previous_gray: np.ndarray | None = None
    smoothed: np.ndarray | None = None
    previous_field: np.ndarray | None = None
    alpha = float(np.exp(-1.0 / max(temporal_sigma, 1e-6))) if temporal_sigma > 0 else 0.0
    field_alpha = float(np.exp(-1.0 / max(field_temporal_sigma, 1e-6))) if field_temporal_sigma > 0 else 0.0

    metrics = RunMetrics(roi)
    coverages: list[float] = []
    processed = 0

    def frames():
        nonlocal previous_gray, smoothed, previous_field, processed
        for frame in _decode_frames(video, width, height, max_frames):
            if external_mask is not None:
                mask = external_mask
                coverage = float(np.count_nonzero(mask > 0.5)) / mask.size * 100.0
            else:
                mask, stats = fallback_skin_mask(frame)
                coverage = float(stats["coverage_pct"])
            coverages.append(coverage)

            gray = cv2.cvtColor((np.clip(frame, 0.0, 1.0) * 255.0).astype(np.uint8),
                                cv2.COLOR_RGB2GRAY)
            if use_flow and previous_gray is not None:
                dx, dy = farneback_flow(previous_gray, gray)
                tracker.update(dx, dy)
            warped = tracker.apply(field) if use_flow else field

            # Temporal low-pass on the warped field, so the flow resampling
            # cannot make the pits shimmer from frame to frame.
            if previous_field is not None and field_alpha > 0:
                warped = (1.0 - field_alpha) * warped + field_alpha * previous_field
            previous_field = warped

            structure = structure_map(
                frame, mask=mask,
                detail_sigma=detail_sigma,
                window_sigma=structure_window,
                threshold=threshold,
                reference=structure_reference,
            )
            amount = structure * mask * strength
            if smoothed is None:
                smoothed = amount
            else:
                smoothed = alpha * smoothed + (1.0 - alpha) * amount

            injected = np.clip(frame + (warped * smoothed)[:, :, None], 0.0, 1.0).astype(np.float32)
            metrics.update(frame, injected)
            previous_gray = gray
            processed += 1
            yield injected

    _write_video(frames(), out, fps, width, height, source=video)

    if not mask_stats:
        mask_stats = {
            "mode": "fallback_prior",
            "coverage_pct": float(np.mean(coverages)) if coverages else 0.0,
            "coverage_pct_min": float(np.min(coverages)) if coverages else 0.0,
            "coverage_pct_max": float(np.max(coverages)) if coverages else 0.0,
        }

    report = {
        "tool": "pore_injector",
        "input": str(video),
        "output": str(out),
        "frames_processed": processed,
        "resolution": [width, height],
        "fps": fps,
        "parameters": {
            "strength": strength,
            "pit_density": pit_density,
            "rim": rim,
            "pore_sigma": pore_sigma,
            "threshold": threshold,
            "mask": str(mask_path) if mask_path else None,
            "temporal_sigma": temporal_sigma,
            "field_temporal_sigma": field_temporal_sigma,
            "seed": seed,
            "two_octave": two_octave,
            "max_frames": max_frames,
            "flow": use_flow,
            "detail_sigma": detail_sigma,
            "structure_window": structure_window,
            "structure_reference": structure_reference,
        },
        "pore_field": dict(field_stats),
        "mask": mask_stats,
        "metrics": metrics.result(),
    }

    if rife_out is not None:
        report["rife"] = _run_rife(out, Path(rife_out))

    if report_path is None:
        report_path = out.with_name(out.stem + "_report.json")
    report_path = Path(report_path)
    report_path.parent.mkdir(parents=True, exist_ok=True)
    with open(report_path, "w", encoding="utf-8") as handle:
        json.dump(report, handle, ensure_ascii=False, indent=2)
    report["report_path"] = str(report_path)

    return report


def _print_report(report: dict) -> None:
    metrics = report["metrics"]
    pore = metrics["pore_band_energy"]
    rgb = metrics["temporal_rgb_delta"]
    hf = metrics["temporal_hf_delta"]
    mask = report["mask"]
    field = report["pore_field"]

    print()
    print("=== PORE INJECTOR REPORT ===")
    print(f"input           : {report['input']}")
    print(f"output          : {report['output']}")
    print(f"frames processed: {report['frames_processed']} "
          f"({report['resolution'][0]}x{report['resolution'][1]} @ {report['fps']:.3f} fps)")
    print(f"pore field      : {field.get('pit_count', 0)} pits "
          f"(fine {field.get('fine_pits', 0)} + coarse {field.get('coarse_pits', 0)}), "
          f"coverage {field.get('coverage_fraction', 0.0) * 100:.1f}% of pixels, "
          f"seed {field.get('seed')}")
    print(f"mask            : mode={mask.get('mode')} coverage {mask.get('coverage_pct', 0.0):.1f}%")
    print(f"pore band energy: {pore['input']:.4e} -> {pore['output']:.4e} "
          f"({pore['delta_pct']:+.1f}%)  [band {pore['band_px'][0]:.0f}-{pore['band_px'][1]:.0f} px]")
    print(f"temporal RGB    : {rgb['input']:.4f} -> {rgb['output']:.4f} ({rgb['delta_pct']:+.1f}%)")
    print(f"temporal HF     : {hf['input']:.4f} -> {hf['output']:.4f} ({hf['delta_pct']:+.1f}%)")
    if "rife" in report:
        rife = report["rife"]
        status = "done" if rife.get("available") else f"SKIPPED ({rife.get('reason')})"
        print(f"RIFE            : {status}")
    print(f"report          : {report.get('report_path', '')}")
    print()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Inject pore STRUCTURE into skin (CPU only, no GPU, no ComfyUI).",
    )
    parser.add_argument("--video", required=True, help="input video")
    parser.add_argument("--out", required=True, help="output video")
    parser.add_argument("--strength", type=float, default=0.07,
                        help="amplitude of a pit core, as a fraction of the 0..1 range")
    parser.add_argument("--pit-density", type=float, default=260.0,
                        help="fine-band pits per 10 000 pixels")
    parser.add_argument("--rim", type=float, default=0.45,
                        help="brightness of the rim around a pit, relative to its depth")
    parser.add_argument("--pore-sigma", type=float, default=1.1,
                        help="core sigma of a fine pit, in pixels")
    parser.add_argument("--threshold", type=float, default=0.55,
                        help="structure threshold: below it, nothing is injected")
    parser.add_argument("--mask", default=None,
                        help="PNG mask (or a directory of PNGs); without it a colour prior is used")
    parser.add_argument("--temporal-sigma", type=float, default=3.0,
                        help="temporal low-pass time constant of the injected AMOUNT, in frames")
    parser.add_argument("--field-temporal-sigma", type=float, default=3.0,
                        help="temporal low-pass time constant of the warped FIELD, in frames")
    parser.add_argument("--structure-reference", default="local",
                        choices=["local", "rank", "percentile"],
                        help="how the structure map is normalised (default: local)")
    parser.add_argument("--seed", type=int, default=1234, help="field seed")
    parser.add_argument("--two-octave", action="store_true", default=False,
                        help="add the sparse coarse band of larger pits")
    parser.add_argument("--single-octave", action="store_true",
                        help="use only the fine band (inverse of --two-octave)")
    parser.add_argument("--max-frames", type=int, default=0,
                        help="process at most N frames (0 = all)")
    parser.add_argument("--no-flow", dest="use_flow", action="store_false", default=True,
                        help="do not warp the field with optical flow")
    parser.add_argument("--flow", dest="use_flow", action="store_true",
                        help="warp the field with optical flow (default)")
    parser.add_argument("--detail-sigma", type=float, default=1.2,
                        help="high-pass sigma used to measure local structure")
    parser.add_argument("--structure-window", type=float, default=3.0,
                        help="window sigma of the local structure measure")
    parser.add_argument("--roi", default=None,
                        help="normalised ROI x0,y0,x1,y1 for the pore-band metric")
    parser.add_argument("--rife-out", default=None,
                        help="also write a RIFE-interpolated copy here, if RIFE is available")
    parser.add_argument("--report", default=None,
                        help="path of the JSON report (default: next to --out)")

    args = parser.parse_args(argv)

    two_octave = True
    if args.two_octave:
        two_octave = True
    if args.single_octave:
        two_octave = False

    roi = None
    if args.roi:
        parts = [float(v) for v in args.roi.split(",")]
        if len(parts) != 4:
            parser.error("--roi needs four numbers: x0,y0,x1,y1")
        roi = (parts[0], parts[1], parts[2], parts[3])

    try:
        report = run_injection(
            video=Path(args.video),
            out=Path(args.out),
            strength=args.strength,
            pit_density=args.pit_density,
            rim=args.rim,
            pore_sigma=args.pore_sigma,
            threshold=args.threshold,
            mask_path=Path(args.mask) if args.mask else None,
            temporal_sigma=args.temporal_sigma,
            field_temporal_sigma=args.field_temporal_sigma,
            seed=args.seed,
            two_octave=two_octave,
            max_frames=args.max_frames,
            use_flow=args.use_flow,
            detail_sigma=args.detail_sigma,
            structure_window=args.structure_window,
            structure_reference=args.structure_reference,
            roi=roi,
            rife_out=Path(args.rife_out) if args.rife_out else None,
            report_path=Path(args.report) if args.report else None,
        )
    except (RuntimeError, FileNotFoundError, ValueError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2

    _print_report(report)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
