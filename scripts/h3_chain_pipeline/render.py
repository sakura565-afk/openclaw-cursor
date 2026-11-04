# -*- coding: utf-8 -*-
"""Talking to ComfyUI: staging frames and calling the renderer.

Two rules of the house, both learned the hard way:

1. ``LoadImage`` takes a FILE NAME, not a path. The runner validates the
   graph against the live ComfyUI before sending it, and the validator
   compares the value with the file list of the server's input directory. A
   full path is not in that list, so the graph is rejected before it is ever
   sent ("workflow rejected before sending: node 1 (LoadImage): input
   'image' value ... not in [...]"). This module therefore copies the frame
   into the input directory itself and hands the runner the bare name.

2. The renderer is ``scripts/h3_i2v_run.py`` and it is not re-implemented
   here. It is called as a subprocess with the same arguments a human would
   type, so its behaviour stays the single source of truth.

The renderer has no ``--server`` flag: the ComfyUI URL comes from the
``COMFYUI_PROD_URL`` environment variable. (The older
``scripts/h3_chain_video.py`` passes ``--server`` to the renderer, which the
renderer's argument parser rejects.) This module sets the environment
variable instead.
"""
from __future__ import annotations

import os
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path

from . import paths

__all__ = [
    "RenderError",
    "RenderRequest",
    "copy_into_input",
    "extract_last_frame",
    "make_silent_wav",
    "render_chunk",
    "video_frame_count",
]


class RenderError(RuntimeError):
    """A chunk could not be rendered."""


@dataclass
class RenderRequest:
    """Everything needed to render one chunk."""

    image_name: str
    """File NAME of the first frame inside the ComfyUI input directory."""
    prompt_file: Path
    audio: Path
    output: Path
    length: int
    seed: int
    width: int
    height: int
    steps: int
    prefix: str
    server: str = ""
    loras_json: str = "[]"
    negative: str = ""
    wait: int = 7200


def copy_into_input(source: Path, input_dir: Path, name: str) -> str:
    """Copy ``source`` into the ComfyUI input directory, return its NAME.

    The name is unique per chunk and per attempt, so two chunks never fight
    over one file in the input directory.
    """
    input_dir = Path(input_dir)
    input_dir.mkdir(parents=True, exist_ok=True)
    target = input_dir / name
    if not target.exists() or target.stat().st_size != source.stat().st_size:
        shutil.copy2(source, target)
    return target.name


def make_silent_wav(path: Path, seconds: float) -> Path:
    """Write a silent WAV track of the given length.

    The renderer requires an ``--audio`` argument and muxes that track into
    the clip it produces. A silent track keeps the chunk's own audio empty,
    so the real soundtrack can be welded onto the assembled clip later
    without the chunk's copy fighting it.
    """
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    seconds = max(1.0, float(seconds))
    cmd = [
        paths.ffmpeg_exe(), "-y", "-v", "error",
        "-f", "lavfi", "-i", "anullsrc=r=48000:cl=stereo",
        "-t", f"{seconds:.3f}", "-c:a", "pcm_s16le", str(path),
    ]
    proc = subprocess.run(cmd, capture_output=True, text=True, timeout=600)
    if proc.returncode != 0 or not path.exists():
        raise RenderError(f"could not write the silent audio track: {proc.stderr[:400]}")
    return path


def video_frame_count(video: Path) -> int:
    """Number of video frames in a file (exact, counted by the decoder)."""
    cmd = [
        paths.ffprobe_exe(), "-v", "error", "-count_frames",
        "-select_streams", "v:0", "-show_entries", "stream=nb_read_frames",
        "-of", "csv=p=0", str(video),
    ]
    proc = subprocess.run(cmd, capture_output=True, text=True, timeout=900)
    value = (proc.stdout or "").strip().split(",")[0]
    try:
        return int(value)
    except ValueError:
        return 0


def extract_last_frame(video: Path, out_png: Path) -> Path:
    """Write the LAST frame of a clip to a PNG.

    The last frame of a chunk is the anchor of the next chunk: it is the
    frame the model itself produced, which is the only thing that keeps the
    face from drifting across the seam.
    """
    out_png = Path(out_png)
    out_png.parent.mkdir(parents=True, exist_ok=True)
    count = video_frame_count(video)
    if count > 0:
        cmd = [
            paths.ffmpeg_exe(), "-y", "-v", "error", "-i", str(video),
            "-vf", f"select=eq(n\\,{count - 1})", "-frames:v", "1",
            "-fps_mode", "passthrough", str(out_png),
        ]
    else:
        # Fallback: seek near the end and keep the last frame that is
        # written out. ``-update 1`` overwrites the same file, so the last
        # frame wins.
        cmd = [
            paths.ffmpeg_exe(), "-y", "-v", "error", "-sseof", "-1",
            "-i", str(video), "-frames:v", "1", "-update", "1", str(out_png),
        ]
    proc = subprocess.run(cmd, capture_output=True, text=True, timeout=900)
    if proc.returncode != 0 or not out_png.exists():
        raise RenderError(f"could not extract the last frame: {proc.stderr[:400]}")
    return out_png


def render_chunk(request: RenderRequest) -> Path:
    """Render one chunk through ``scripts/h3_i2v_run.py``.

    Returns the path of the rendered clip. The renderer is called as a
    subprocess so its own argument parsing, validation and error reporting
    stay authoritative.
    """
    runner = paths.RUNNER
    if not runner.exists():
        raise RenderError(f"the renderer is missing: {runner}")
    cmd = [
        paths.python_exe(), "-u", str(runner),
        "--image", request.image_name,
        "--width", str(request.width),
        "--height", str(request.height),
        "--length", str(request.length),
        "--seed", str(request.seed),
        "--steps", str(request.steps),
        "--prompt-file", str(request.prompt_file),
        "--prefix", request.prefix,
        "--audio", str(request.audio),
        "--output", str(request.output),
        "--wait", str(request.wait),
    ]
    if request.loras_json and request.loras_json != "[]":
        cmd += ["--loras-json", request.loras_json]
    if request.negative:
        cmd += ["--negative", request.negative]

    env = dict(os.environ)
    env["PYTHONPATH"] = str(paths.REPO_ROOT)
    if request.server:
        env["COMFYUI_PROD_URL"] = request.server
    request.output.parent.mkdir(parents=True, exist_ok=True)
    proc = subprocess.run(
        cmd, capture_output=True, text=True, timeout=request.wait + 600,
        cwd=str(paths.REPO_ROOT), env=env,
    )
    tail = ((proc.stdout or "")[-2000:] + (proc.stderr or "")[-800:]).strip()
    if proc.returncode != 0:
        raise RenderError(
            f"the renderer failed (exit {proc.returncode}) for seed "
            f"{request.seed}:\n{tail[-1200:]}"
        )
    if not request.output.exists():
        raise RenderError(
            f"the renderer produced no file for seed {request.seed}:\n{tail[-1200:]}"
        )
    return request.output
