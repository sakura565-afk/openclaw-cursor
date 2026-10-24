"""Multi-clip video assembly: concat, AR normalisation, optional RIFE and upscale."""

from __future__ import annotations

import argparse
import copy
import json
import re
import shutil
import subprocess
import tempfile
import time
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Sequence

import requests

DEFAULT_FPS = 24
DEFAULT_CRF = 20
X264_PRESET = "fast"
AUDIO_BITRATE = "192k"
AUDIO_SAMPLE_RATE = 48000
AUDIO_CHANNELS = 2
VIDEO_TIMESCALE = 90000
DEFAULT_COMFY_URL = "http://127.0.0.1:8188"
POLL_INTERVAL_SEC = 2.0
POLL_TIMEOUT_SEC = 1800


class AssemblyError(RuntimeError):
    """Raised when assembly or ffmpeg operations fail."""


@dataclass
class ClipSpec:
    """Single input clip for assembly."""

    path: Path


@dataclass
class AssemblySpec:
    """Full assembly job specification."""

    clips: list[ClipSpec]
    out_path: Path
    target_ar: float | str | None = None
    target_fps: int | None = None
    interpolate: bool = False
    upscale_to: tuple[int, int] | None = None


def _run_ffprobe(args: list[str], timeout: int = 10) -> subprocess.CompletedProcess[str]:
    """Run ffprobe with capture and no check."""
    return subprocess.run(
        ["ffprobe", *args],
        check=False,
        capture_output=True,
        text=True,
        timeout=timeout,
    )


def _run_ffmpeg(cmd: list[str], operation: str, timeout: int | None = 600) -> None:
    """Run ffmpeg argv list; raise AssemblyError with stderr on failure."""
    proc = subprocess.run(
        cmd,
        check=False,
        capture_output=True,
        text=True,
        timeout=timeout,
    )
    if proc.returncode != 0:
        raise AssemblyError(
            f"{operation}: ffmpeg exited {proc.returncode}\n{proc.stderr.strip()}"
        )


def _parse_fps(value: str | None) -> float:
    if not value:
        return 0.0
    if "/" in value:
        num, den = value.split("/", 1)
        den_f = float(den)
        return float(num) / den_f if den_f else 0.0
    try:
        return float(value)
    except ValueError:
        return 0.0


def _get_video_stream(info: dict[str, Any]) -> dict[str, Any]:
    for stream in info.get("streams", []):
        if stream.get("codec_type") == "video":
            return stream
    raise AssemblyError("No video stream found in ffprobe output")


def _get_audio_stream(info: dict[str, Any]) -> dict[str, Any] | None:
    for stream in info.get("streams", []):
        if stream.get("codec_type") == "audio":
            return stream
    return None


def probe(path: Path) -> dict[str, Any]:
    """Probe a media file: width, height, duration, fps, has_audio."""
    proc = _run_ffprobe(
        [
            "-v",
            "quiet",
            "-print_format",
            "json",
            "-show_streams",
            "-show_format",
            str(path),
        ]
    )
    if proc.returncode != 0:
        raise AssemblyError(f"ffprobe failed for {path}: {proc.stderr.strip()}")
    info = json.loads(proc.stdout)
    video = _get_video_stream(info)
    audio = _get_audio_stream(info)
    fmt = info.get("format", {})
    return {
        "width": int(video.get("width", 0)),
        "height": int(video.get("height", 0)),
        "duration": float(fmt.get("duration", 0.0)),
        "fps": _parse_fps(video.get("avg_frame_rate") or video.get("r_frame_rate")),
        "has_audio": audio is not None,
    }


def parse_ar(text: str | float) -> float:
    """Parse aspect ratio from ``9:16``, ``16:9``, ``1:1``, a float string, or a float."""
    if isinstance(text, (int, float)):
        return float(text)
    raw = str(text).strip()
    if ":" in raw:
        left, right = raw.split(":", 1)
        w = float(left)
        h = float(right)
        if h == 0:
            raise AssemblyError(f"Invalid aspect ratio: {text}")
        return w / h
    return float(raw)


def _even(value: float) -> int:
    """Nearest even integer at least 2 (H.264 requires even dimensions)."""
    number = int(round(value))
    if number < 2:
        number = 2
    if number % 2:
        number += 1
    return number


def _clip_metrics(clips: Sequence[ClipSpec | dict[str, Any]]) -> list[dict[str, Any]]:
    metrics: list[dict[str, Any]] = []
    for item in clips:
        if isinstance(item, ClipSpec):
            metrics.append(probe(item.path))
        else:
            metrics.append(item)
    return metrics


def target_resolution(
    clips: Sequence[ClipSpec | dict[str, Any]],
    target_ar: float | None = None,
) -> tuple[int, int]:
    """Pick output width/height for scale+pad; default AR is the maximum input AR."""
    specs = _clip_metrics(clips)
    if not specs:
        raise AssemblyError("target_resolution: no clips provided")

    if target_ar is None:
        target_ar = max(s["width"] / s["height"] for s in specs if s["height"])

    max_w = max(s["width"] for s in specs)
    max_h = max(s["height"] for s in specs)

    height = _even(max_h)
    width = _even(height * target_ar)
    if width < max_w:
        width = _even(max_w)
        height = _even(width / target_ar)
    return width, height


def scale_pad_filter(width: int, height: int, fps: int | None = None) -> str:
    """Video filter: scale with decrease, centred pad, setsar, optional fps."""
    parts = [
        f"scale={width}:{height}:force_original_aspect_ratio=decrease",
        f"pad={width}:{height}:(ow-iw)/2:(oh-ih)/2:color=black",
        "setsar=1",
    ]
    if fps:
        parts.append(f"fps={fps}")
    return ",".join(parts)


def _audio_encode_args() -> list[str]:
    return [
        "-c:a",
        "aac",
        "-b:a",
        AUDIO_BITRATE,
        "-ar",
        str(AUDIO_SAMPLE_RATE),
        "-ac",
        str(AUDIO_CHANNELS),
    ]


def build_normalize_argv(
    src: Path,
    dst: Path,
    *,
    width: int,
    height: int,
    fps: int,
    has_audio: bool,
    ensure_audio: bool,
) -> list[str]:
    """Build argv to normalise one clip before concat demuxer."""
    cmd: list[str] = ["ffmpeg", "-y", "-v", "error", "-i", str(src)]
    add_silence = ensure_audio and not has_audio
    if add_silence:
        cmd += [
            "-f",
            "lavfi",
            "-i",
            f"anullsrc=channel_layout=stereo:sample_rate={AUDIO_SAMPLE_RATE}",
        ]
    cmd += ["-map", "0:v:0"]
    if add_silence:
        cmd += ["-map", "1:a:0"]
    elif has_audio:
        cmd += ["-map", "0:a:0"]
    cmd += [
        "-vf",
        scale_pad_filter(width, height, fps),
        "-r",
        str(fps),
        "-c:v",
        "libx264",
        "-preset",
        X264_PRESET,
        "-crf",
        str(DEFAULT_CRF),
        "-pix_fmt",
        "yuv420p",
        "-video_track_timescale",
        str(VIDEO_TIMESCALE),
    ]
    if has_audio or add_silence:
        cmd += _audio_encode_args()
        if add_silence:
            cmd += ["-shortest"]
    else:
        cmd += ["-an"]
    cmd.append(str(dst))
    return cmd


def build_concat_demuxer_argv(list_file: Path, dst: Path) -> list[str]:
    """Build argv for stream-copy concat after normalisation."""
    return [
        "ffmpeg",
        "-y",
        "-v",
        "error",
        "-f",
        "concat",
        "-safe",
        "0",
        "-i",
        str(list_file),
        "-c",
        "copy",
        "-movflags",
        "+faststart",
        str(dst),
    ]


def _concat_list_text(paths: Sequence[Path]) -> str:
    lines = []
    for item in paths:
        escaped = str(item).replace("'", "'\\''")
        lines.append(f"file '{escaped}'")
    return "\n".join(lines) + "\n"


def concat_clips(
    clips: Sequence[ClipSpec],
    out_path: Path,
    target_ar: float | None = None,
) -> Path:
    """Concatenate clips with per-segment audio (normalize each clip, then concat demuxer)."""
    if not clips:
        raise AssemblyError("concat_clips: no clips provided")

    clip_paths = [Path(c.path) for c in clips]
    infos = [probe(p) for p in clip_paths]
    width, height = target_resolution(infos, target_ar=target_ar)
    target_fps = max(1, int(round(max(i["fps"] for i in infos) or DEFAULT_FPS)))
    needs_audio = any(i["has_audio"] for i in infos)

    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)

    temp_root = Path(tempfile.mkdtemp(prefix="vp-assemble-"))
    try:
        normalized: list[Path] = []
        for index, (clip_path, info) in enumerate(zip(clip_paths, infos)):
            staged = temp_root / f"part_{index:03d}.mp4"
            cmd = build_normalize_argv(
                clip_path,
                staged,
                width=width,
                height=height,
                fps=target_fps,
                has_audio=info["has_audio"],
                ensure_audio=needs_audio,
            )
            _run_ffmpeg(cmd, f"concat_clips normalize[{index}]")
            normalized.append(staged)

        list_file = temp_root / "concat_list.txt"
        list_file.write_text(_concat_list_text(normalized), encoding="utf-8", newline="\n")
        _run_ffmpeg(build_concat_demuxer_argv(list_file, out_path), "concat_clips")
    finally:
        shutil.rmtree(temp_root, ignore_errors=True)

    return out_path


def upscale(in_path: Path, out_path: Path, width: int, height: int) -> Path:
    """Upscale with Lanczos scale+pad preserving aspect ratio."""
    in_path = Path(in_path)
    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    vf = (
        f"scale={width}:{height}:force_original_aspect_ratio=decrease,"
        f"pad={width}:{height}:(ow-iw)/2:(oh-ih)/2:color=black,setsar=1"
    )
    cmd = [
        "ffmpeg",
        "-y",
        "-v",
        "error",
        "-i",
        str(in_path),
        "-vf",
        vf,
        "-c:v",
        "libx264",
        "-preset",
        X264_PRESET,
        "-crf",
        str(DEFAULT_CRF),
        "-pix_fmt",
        "yuv420p",
        "-c:a",
        "copy",
        str(out_path),
    ]
    _run_ffmpeg(cmd, "upscale")
    return out_path


def _comfy_reachable(comfyui_url: str) -> bool:
    try:
        response = requests.get(f"{comfyui_url.rstrip('/')}/system_stats", timeout=5)
        return response.status_code < 500
    except requests.RequestException:
        return False


def _default_rife_workflow(video_filename: str, target_fps: float) -> dict[str, Any]:
    """Minimal ComfyUI API workflow for VHS + RIFE-style interpolation."""
    return {
        "1": {
            "class_type": "VHS_LoadVideo",
            "inputs": {
                "video": video_filename,
                "force_rate": 0,
                "custom_width": 0,
                "custom_height": 0,
                "frame_load_cap": 0,
                "skip_first_frames": 0,
                "select_every_nth": 1,
            },
        },
        "2": {
            "class_type": "RIFE VFI",
            "inputs": {
                "ckpt_name": "rife47.pth",
                "multiplier": 2,
                "fast_mode": True,
                "ensemble": True,
                "scale_factor": 1.0,
                "frames": ["1", 0],
            },
        },
        "3": {
            "class_type": "VHS_VideoCombine",
            "inputs": {
                "frame_rate": target_fps,
                "loop_count": 0,
                "filename_prefix": "assembler_rife",
                "format": "video/h264-mp4",
                "pingpong": False,
                "save_output": True,
                "images": ["2", 0],
            },
        },
    }


def _upload_video(comfyui_url: str, video_path: Path) -> str:
    url = f"{comfyui_url.rstrip('/')}/upload/image"
    with video_path.open("rb") as handle:
        files = {"image": (video_path.name, handle, "video/mp4")}
        data = {"subfolder": "", "type": "input"}
        response = requests.post(url, files=files, data=data, timeout=120)
    if response.status_code >= 400:
        raise AssemblyError(
            f"ComfyUI upload failed ({response.status_code}): {response.text.strip()}"
        )
    payload = response.json()
    return str(payload.get("name", video_path.name))


def _submit_prompt(comfyui_url: str, workflow: dict[str, Any]) -> str:
    client_id = str(uuid.uuid4())
    body = {"prompt": workflow, "client_id": client_id}
    response = requests.post(
        f"{comfyui_url.rstrip('/')}/prompt",
        json=body,
        timeout=60,
    )
    if response.status_code >= 400:
        raise AssemblyError(f"ComfyUI /prompt failed ({response.status_code}): {response.text.strip()}")
    data = response.json()
    prompt_id = data.get("prompt_id")
    if not prompt_id:
        raise AssemblyError(f"ComfyUI did not return prompt_id: {data}")
    return str(prompt_id)


def _download_first_video(comfyui_url: str, entry: dict[str, Any], out_path: Path) -> None:
    outputs = entry.get("outputs", {})
    for node_output in outputs.values():
        for key in ("videos", "gifs", "images"):
            items = node_output.get(key)
            if not items:
                continue
            video_info = items[0] if isinstance(items, list) else items
            if not isinstance(video_info, dict):
                continue
            filename = video_info.get("filename")
            if not filename:
                continue
            params = {
                "filename": filename,
                "subfolder": video_info.get("subfolder", ""),
                "type": video_info.get("type", "output"),
            }
            response = requests.get(
                f"{comfyui_url.rstrip('/')}/view",
                params=params,
                timeout=120,
            )
            if response.status_code >= 400:
                continue
            out_path.parent.mkdir(parents=True, exist_ok=True)
            out_path.write_bytes(response.content)
            return
    raise AssemblyError("ComfyUI history contained no downloadable video output")


def interpolate(
    in_path: Path,
    out_path: Path,
    target_fps: float,
    comfyui_url: str = DEFAULT_COMFY_URL,
    workflow_json: dict[str, Any] | None = None,
) -> Path:
    """Run RIFE interpolation via ComfyUI; raises if ComfyUI is unreachable."""
    in_path = Path(in_path)
    out_path = Path(out_path)
    if not _comfy_reachable(comfyui_url):
        raise AssemblyError(
            f"ComfyUI is not reachable at {comfyui_url}; cannot interpolate. "
            "Keep the non-interpolated file."
        )

    uploaded_name = _upload_video(comfyui_url, in_path)
    workflow = copy.deepcopy(workflow_json) if workflow_json else _default_rife_workflow(
        uploaded_name, target_fps
    )
    prompt_id = _submit_prompt(comfyui_url, workflow)

    started = time.time()
    while time.time() - started < POLL_TIMEOUT_SEC:
        response = requests.get(
            f"{comfyui_url.rstrip('/')}/history/{prompt_id}",
            timeout=30,
        )
        if response.status_code >= 400:
            raise AssemblyError(f"ComfyUI /history failed ({response.status_code}): {response.text.strip()}")
        history = response.json()
        if prompt_id not in history:
            time.sleep(POLL_INTERVAL_SEC)
            continue
        entry = history[prompt_id]
        status = entry.get("status", {})
        if status.get("status_str") == "error":
            raise AssemblyError(f"ComfyUI RIFE failed: {status.get('messages')}")
        if entry.get("outputs") or status.get("status_str") == "success":
            _download_first_video(comfyui_url, entry, out_path)
            return out_path
        time.sleep(POLL_INTERVAL_SEC)

    raise AssemblyError(f"ComfyUI RIFE timed out after {POLL_TIMEOUT_SEC}s for prompt_id={prompt_id}")


def assemble(spec: AssemblySpec) -> Path:
    """Concatenate clips, optionally interpolate and upscale."""
    target_ar_value: float | None = None
    if spec.target_ar is not None:
        target_ar_value = parse_ar(spec.target_ar)

    current = spec.out_path
    temps: list[Path] = []
    out_path = Path(spec.out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)

    try:
        concat_target = out_path if not (spec.interpolate or spec.upscale_to) else (
            out_path.with_name(f"{out_path.stem}_concat{out_path.suffix}")
        )
        if concat_target != out_path:
            temps.append(concat_target)
        concat_clips(spec.clips, concat_target, target_ar=target_ar_value)
        current = concat_target

        if spec.interpolate:
            if spec.target_fps is None:
                raise AssemblyError("assemble: target_fps is required when interpolate=True")
            interp_target = (
                out_path
                if not spec.upscale_to
                else out_path.with_name(f"{out_path.stem}_interp{out_path.suffix}")
            )
            if interp_target != out_path:
                temps.append(interp_target)
            interpolate(current, interp_target, float(spec.target_fps))
            current = interp_target

        if spec.upscale_to:
            upscale(current, out_path, spec.upscale_to[0], spec.upscale_to[1])
            current = out_path
        elif current != out_path:
            shutil.move(str(current), str(out_path))
            current = out_path

        return out_path
    finally:
        for temp in temps:
            if temp.exists() and temp != out_path:
                temp.unlink(missing_ok=True)


def _parse_upscale(text: str) -> tuple[int, int]:
    match = re.fullmatch(r"(\d+)x(\d+)", text.strip())
    if not match:
        raise AssemblyError(f"Invalid upscale dimensions: {text} (expected WIDTHxHEIGHT)")
    return int(match.group(1)), int(match.group(2))


def _build_cli_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Assemble multiple clips into one deliverable.")
    parser.add_argument("--inputs", nargs="+", required=True, help="Input clip paths in order.")
    parser.add_argument("--out", required=True, help="Output mp4 path.")
    parser.add_argument("--target-ar", default=None, help="Target aspect ratio (e.g. 9:16).")
    parser.add_argument("--target-fps", type=int, default=None, help="Target fps for RIFE.")
    parser.add_argument("--upscale", default=None, help="Upscale target WxH, e.g. 1080x1920.")
    parser.add_argument("--interpolate", action="store_true", help="Run ComfyUI RIFE interpolation.")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _build_cli_parser().parse_args(list(argv) if argv is not None else None)
    clips = [ClipSpec(path=Path(p)) for p in args.inputs]
    spec = AssemblySpec(
        clips=clips,
        out_path=Path(args.out),
        target_ar=args.target_ar,
        target_fps=args.target_fps,
        interpolate=args.interpolate,
        upscale_to=_parse_upscale(args.upscale) if args.upscale else None,
    )
    result = assemble(spec)
    print(result)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
