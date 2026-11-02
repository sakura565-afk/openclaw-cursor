"""CLI: product photo -> FreeVideo promo clip (LAB ComfyUI)."""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

from loguru import logger

from video_pipeline.assembler import interpolate, probe
from video_pipeline.config import FreeVideoConfig, OutputSpec, UpscaleConfig
from video_pipeline.director_base import JobSpec
from video_pipeline.directors.freevideo import (
    FRAME_LOCK_CLAUSE,
    FreeVideoDirector,
    FreeVideoDirectorError,
)
from video_pipeline.upscale import mux_audio_from_source, upscale_video
from video_pipeline.vram_guard import (
    acquire_render_lock,
    pause_named_processes,
    release_render_lock,
    restore_processes,
)

DEFAULT_COMFY_URL = "http://127.0.0.1:8189"
DEFAULT_SECONDS = 7.29
DEFAULT_SEED = 42
DEFAULT_TIMEOUT = 5400
MIN_STABLE_BYTES = 100 * 1024
STABLE_CHECKS = 3
POLL_INTERVAL_SEC = 1.0


def build_parser() -> argparse.ArgumentParser:
    """Build the product-shot CLI argument parser."""
    parser = argparse.ArgumentParser(
        description="Render a product promo clip from a photo via FreeVideo (LAB ComfyUI).",
    )
    parser.add_argument("--image", type=Path, required=True, help="First-frame source image.")
    parser.add_argument(
        "--last-image",
        type=Path,
        default=None,
        help="Optional last-frame image pinned into the FreeVideo graph.",
    )
    parser.add_argument("--width", type=int, required=True, help="Render width in pixels.")
    parser.add_argument("--height", type=int, required=True, help="Render height in pixels.")
    parser.add_argument(
        "--seconds",
        type=float,
        default=DEFAULT_SECONDS,
        help=f"Clip length in seconds (default {DEFAULT_SECONDS}).",
    )
    parser.add_argument(
        "--prompt",
        type=Path,
        required=True,
        help="Text file containing the generation prompt.",
    )
    parser.add_argument("--out", type=Path, required=True, help="Destination .mp4 path.")
    parser.add_argument("--seed", type=int, default=DEFAULT_SEED, help=f"Seed (default {DEFAULT_SEED}).")
    parser.add_argument(
        "--timeout",
        type=int,
        default=DEFAULT_TIMEOUT,
        help=f"ComfyUI poll timeout in seconds (default {DEFAULT_TIMEOUT}).",
    )
    parser.add_argument(
        "--comfy-url",
        default=DEFAULT_COMFY_URL,
        help=f"LAB ComfyUI base URL (default {DEFAULT_COMFY_URL}).",
    )
    parser.add_argument(
        "--free-url",
        action="append",
        default=None,
        help=(
            "Extra ComfyUI instance whose cached models must be released before the render "
            "(repeatable). The FreeVideo worker keeps ~13 GB of weights in VRAM; an idle "
            "instance holding models slows the render down by ~80x."
        ),
    )
    parser.add_argument(
        "--no-free",
        action="store_true",
        help="Do not release VRAM before rendering (opt out of the default preflight).",
    )
    parser.add_argument(
        "--interpolate",
        type=float,
        default=None,
        help="Interpolate the render to this fps with RIFE (PROD ComfyUI), e.g. 48.",
    )
    parser.add_argument(
        "--interpolate-url",
        default="http://127.0.0.1:8188",
        help="ComfyUI instance that runs RIFE (default http://127.0.0.1:8188).",
    )
    parser.add_argument(
        "--upscale",
        default=None,
        choices=["seedvr2", "lanczos"],
        help="Upscale the result after rendering (seedvr2 on PROD, or plain lanczos).",
    )
    parser.add_argument(
        "--upscale-height",
        type=int,
        default=None,
        help="Target height for the upscale (width follows the source AR).",
    )
    parser.add_argument(
        "--upscale-url",
        default="http://127.0.0.1:8188",
        help="ComfyUI instance that runs the upscaler (default http://127.0.0.1:8188).",
    )
    parser.add_argument(
        "--pause-process",
        action="append",
        default=None,
        help=(
            "Executable name of a GPU-holding process to stop for the duration of the render "
            "(repeatable), e.g. --pause-process llama-server.exe. FreeVideo needs ~13 GB of "
            "VRAM for its weights; a local LLM server holding VRAM makes the render ~80x "
            "slower. The process is restarted with its original command line afterwards."
        ),
    )
    parser.add_argument(
        "--no-two-pass",
        action="store_true",
        help="Single sampling pass instead of the default base pass + refine pass.",
    )
    parser.add_argument(
        "--base-steps",
        type=int,
        default=None,
        help="Base pass denoising steps (engine default: 8).",
    )
    parser.add_argument(
        "--refine-steps",
        type=int,
        default=None,
        help="Refine pass denoising steps (engine default: 3).",
    )
    parser.add_argument(
        "--tier",
        choices=["light", "medium", "high", "max"],
        default=None,
        help=(
            "FreeVideo quality tier. light = two_pass + upscale_factor 2 (first pass at "
            "25 percent of pixels - waxy skin, the micro-texture cannot be born there); "
            "medium = two_pass + upscale_factor 1.5 (44 percent of pixels); high = single pass, "
            "16 steps, full resolution; max = single pass, 20 steps, full resolution. "
            "Overrides --no-two-pass / --base-steps / --step-cache."
        ),
    )
    parser.add_argument(
        "--step-cache",
        default=None,
        help="FreeVideo step cache mode (e.g. easycache-av). Used by the high/max tiers.",
    )
    parser.add_argument(
        "--upscale-factor",
        type=float,
        default=None,
        help="Two-pass learned upscale factor: 2.0 = light, 1.5 = medium.",
    )
    parser.add_argument(
        "--first-frame-reference",
        action="store_true",
        help="Ask the engine to treat the first frame as a reference (reduces drift).",
    )
    return parser


def _resolve_watch_path(candidate: Path) -> Path:
    """Return ``candidate`` or fall back to ``video.mp4`` in the same directory."""
    if candidate.exists():
        return candidate
    fallback = candidate.parent / "video.mp4"
    if fallback.exists():
        return fallback
    return candidate


def wait_for_stable_file(
    path: Path,
    *,
    min_bytes: int = MIN_STABLE_BYTES,
    stable_checks: int = STABLE_CHECKS,
    poll_interval: float = POLL_INTERVAL_SEC,
) -> Path:
    """Poll until ``path`` size is unchanged for ``stable_checks`` reads and above ``min_bytes``."""
    last_size: int | None = None
    stable_count = 0
    watch = path

    while True:
        watch = _resolve_watch_path(watch)
        if not watch.exists():
            fallback = watch.parent / "video.mp4"
            if fallback.exists() and fallback != watch:
                watch = fallback
                last_size = None
                stable_count = 0
            else:
                time.sleep(poll_interval)
                continue

        size = watch.stat().st_size
        if last_size is not None and size == last_size and size > min_bytes:
            stable_count += 1
            if stable_count >= stable_checks:
                return watch
        else:
            stable_count = 0
        last_size = size
        time.sleep(poll_interval)


def copy_when_stable(source: Path, dest: Path) -> Path:
    """Wait for a stable render file, then copy it to ``dest``."""
    stable = wait_for_stable_file(source)
    dest.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(stable, dest)
    return dest


def discover_new_render_mp4(output_root: Path, before: set[Path]) -> Path | None:
    """Return the newest mp4 that appeared under ``output_root`` since ``before``."""
    after = FreeVideoDirector.snapshot_mp4_files(output_root)
    return FreeVideoDirector.discover_newest_mp4(before, after)


def probe_video_summary(path: Path) -> dict[str, object]:
    """Read size, fps, frame count, duration, and audio presence via ffprobe."""
    size = path.stat().st_size
    proc = subprocess.run(
        [
            "ffprobe",
            "-v",
            "quiet",
            "-print_format",
            "json",
            "-show_format",
            "-show_streams",
            str(path),
        ],
        check=False,
        capture_output=True,
        text=True,
        timeout=30,
    )
    summary: dict[str, object] = {
        "path": str(path),
        "size_bytes": size,
        "fps": None,
        "frames": None,
        "duration_sec": None,
        "has_audio": False,
    }
    if proc.returncode != 0:
        return summary

    data = json.loads(proc.stdout)
    fmt = data.get("format", {})
    summary["duration_sec"] = float(fmt.get("duration", 0.0) or 0.0)

    video_stream: dict[str, object] | None = None
    for stream in data.get("streams", []):
        if stream.get("codec_type") == "audio":
            summary["has_audio"] = True
        if stream.get("codec_type") == "video" and video_stream is None:
            video_stream = stream

    if video_stream:
        nb = video_stream.get("nb_frames")
        if nb is not None:
            summary["frames"] = int(nb)
        fps_raw = video_stream.get("avg_frame_rate") or video_stream.get("r_frame_rate")
        if isinstance(fps_raw, str) and "/" in fps_raw:
            num, den = fps_raw.split("/", 1)
            if float(den) != 0:
                summary["fps"] = float(num) / float(den)
        duration = video_stream.get("duration")
        if summary["duration_sec"] == 0.0 and duration is not None:
            summary["duration_sec"] = float(duration)

    return summary


def run_render(
    *,
    image: Path,
    last_image: Path | None,
    width: int,
    height: int,
    seconds: float,
    prompt_path: Path,
    out: Path,
    seed: int,
    timeout: int,
    comfy_url: str,
    free_urls: list[str] | None = None,
    no_free: bool = False,
    no_two_pass: bool = False,
    base_steps: int | None = None,
    refine_steps: int | None = None,
    tier: str | None = None,
    step_cache: str | None = None,
    upscale_factor: float | None = None,
    first_frame_reference: bool = False,
    pause_process: list[str] | None = None,
    interpolate_fps: float | None = None,
    interpolate_url: str = "http://127.0.0.1:8188",
    upscale_backend: str | None = None,
    upscale_height: int | None = None,
    upscale_url: str = "http://127.0.0.1:8188",
) -> dict[str, object]:
    """Run FreeVideo and copy a stable mp4 to ``out``."""
    if not image.exists():
        raise FreeVideoDirectorError(f"Source image not found: {image}")
    if not prompt_path.exists():
        raise FreeVideoDirectorError(f"Prompt file not found: {prompt_path}")

    prompt_text = prompt_path.read_text(encoding="utf-8").strip()
    if not prompt_text:
        raise FreeVideoDirectorError(f"Prompt file is empty: {prompt_path}")

    two_pass = not no_two_pass
    eff_base_steps = base_steps
    eff_refine_steps = refine_steps
    eff_step_cache = step_cache
    eff_upscale_factor = upscale_factor
    if tier:
        # tiers.py: light = two_pass + upscale 2; medium = two_pass + upscale 1.5;
        # high = single pass 16 steps + easycache-av; max = single pass 20 steps + easycache-av.
        if tier == "light":
            two_pass = True
            eff_upscale_factor = 2.0
        elif tier == "medium":
            two_pass = True
            eff_upscale_factor = 1.5
        else:
            two_pass = False
            eff_base_steps = 16 if tier == "high" else 20
            eff_step_cache = "easycache-av"
    fv_config = FreeVideoConfig(
        width=width,
        height=height,
        seconds=seconds,
        seed=seed,
        last_frame=last_image,
        frame_lock=True,
        two_pass=two_pass,
        first_frame_reference=first_frame_reference,
        **({"base_steps": eff_base_steps} if eff_base_steps is not None else {}),
        **({"refine_steps": eff_refine_steps} if eff_refine_steps is not None else {}),
        **({"step_cache": eff_step_cache} if eff_step_cache else {}),
        **({"upscale_factor": eff_upscale_factor} if eff_upscale_factor is not None else {}),
    )
    work_dir = out.parent / ".product_shot_work"
    director = FreeVideoDirector(
        output_dir=work_dir,
        comfy_url=comfy_url,
        freevideo_config=fv_config,
        free_vram_urls=list(free_urls or []),
        free_vram_before_submit=not no_free,
    )

    director._ensure_freevideo_installed()
    uploaded_name = director._upload_image(image)
    last_uploaded_name: str | None = None
    extra: dict[str, object] = {"uploaded_name": uploaded_name}
    if last_image is not None:
        if not last_image.exists():
            raise FreeVideoDirectorError(f"last_frame file not found: {last_image}")
        last_uploaded_name = director._upload_image(last_image)
        extra["last_frame"] = last_uploaded_name

    workflow_prompt = prompt_text
    if fv_config.frame_lock:
        workflow_prompt += FRAME_LOCK_CLAUSE

    workflow = director.build_workflow(uploaded_name, workflow_prompt, last_uploaded_name)
    output_spec = OutputSpec(format="16:9", motion="slow_push_in", duration_sec=max(1, int(seconds)))
    length_frames = director._length_frames(seconds)
    interim = work_dir / f"{image.stem}_render.mp4"
    job = JobSpec(
        source_image=image,
        output_spec=output_spec,
        workflow=workflow,
        output_path=interim,
        prompt_text=workflow_prompt,
        width=width,
        height=height,
        length_frames=length_frames,
        extra=extra,
    )

    snapshot_before = director.snapshot_mp4_files(director.output_root)
    lock_path = acquire_render_lock("freevideo render") if pause_process else None
    paused = pause_named_processes(list(pause_process or []))
    if paused:
        logger.info(
            "Paused {} GPU-holding process(es) for the render: {}",
            len(paused),
            [p.name for p in paused],
        )
    if lock_path is not None:
        logger.info("Render lock active: {}", lock_path)
    try:
        prompt_id = director.submit(job)
        director.poll_until_done_with_job(prompt_id, timeout, job)
    finally:
        if paused:
            restored = restore_processes(paused)
            logger.info("Restored {} paused process(es): {}", len(restored), [p.name for p in paused])
        if lock_path is not None:
            release_render_lock()

    render_mp4 = discover_new_render_mp4(director.output_root, snapshot_before)
    if render_mp4 is None:
        render_mp4 = job.output_path
    partial = render_mp4.parent / "video.partial.mp4"
    watch = partial if partial.exists() else render_mp4
    copy_when_stable(watch, out)

    final = post_process(
        out,
        interpolate_fps=interpolate_fps,
        interpolate_url=interpolate_url,
        upscale_backend=upscale_backend,
        upscale_height=upscale_height,
        upscale_url=upscale_url,
    )

    return probe_video_summary(final)


def post_process(
    src: Path,
    *,
    interpolate_fps: float | None = None,
    interpolate_url: str = "http://127.0.0.1:8188",
    upscale_backend: str | None = None,
    upscale_height: int | None = None,
    upscale_url: str = "http://127.0.0.1:8188",
) -> Path:
    """Run the optional post chain on a rendered clip and return the final path.

    Interpolate -> upscale. Both stages write video frames only, so the original
    audio track is muxed back after every stage.
    """
    src = Path(src)
    final = src
    if interpolate_fps:
        interp = src.with_name(f"{src.stem}_interp{src.suffix}")
        interpolate(final, interp, interpolate_fps, comfyui_url=interpolate_url)
        final = mux_audio_from_source(interp, src, src.with_name(f"{src.stem}_ip{src.suffix}"))
        logger.info("Interpolated to {} fps -> {}", interpolate_fps, final)
    if upscale_backend:
        # A failed learned upscale must never silently degrade to a Lanczos resize: the
        # output keeps the right resolution and the audio, so it is indistinguishable
        # from a real upscale by looking at the file. Fail loudly instead (the caller
        # can override by exporting UPSCALE_NO_FALLBACK=0 before running).
        os.environ.setdefault("UPSCALE_NO_FALLBACK", "1")
        info = probe(final)
        src_h = int(info.get("height") or 0) or 1
        src_w = int(info.get("width") or 0) or 1
        height = upscale_height or src_h
        width = max(32, int(round(height * src_w / src_h / 32)) * 32)
        target = src.with_name(f"{src.stem}_up{src.suffix}")
        upscale_video(
            final,
            target,
            width,
            height,
            UpscaleConfig(backend=upscale_backend, comfyui_url=upscale_url),
        )
        final = mux_audio_from_source(target, final, src.with_name(f"{src.stem}_final{src.suffix}"))
        logger.info("Upscaled to {}x{} -> {}", width, height, final)
    return final


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    try:
        summary = run_render(
            image=args.image,
            last_image=args.last_image,
            width=args.width,
            height=args.height,
            seconds=args.seconds,
            prompt_path=args.prompt,
            out=args.out,
            seed=args.seed,
            timeout=args.timeout,
            comfy_url=args.comfy_url,
            free_urls=args.free_url,
            no_free=args.no_free,
            no_two_pass=args.no_two_pass,
            base_steps=args.base_steps,
            refine_steps=args.refine_steps,
            tier=args.tier,
            step_cache=args.step_cache,
            upscale_factor=args.upscale_factor,
            first_frame_reference=args.first_frame_reference,
            pause_process=args.pause_process,
            interpolate_fps=args.interpolate,
            interpolate_url=args.interpolate_url,
            upscale_backend=args.upscale,
            upscale_height=args.upscale_height,
            upscale_url=args.upscale_url,
        )
    except FreeVideoDirectorError as exc:
        logger.error("{}", exc)
        return 1

    print(json.dumps(summary, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
