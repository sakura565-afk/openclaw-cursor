# -*- coding: utf-8 -*-
"""Machine-independent path resolution for the H3 chain pipeline.

Nothing here is machine specific: every directory comes from the environment
or from the location of this file. The ComfyUI input directory is resolved in
the same order ``scripts/h3_i2v_run.py`` uses it, because the runner's
``LoadImage`` node is validated against the files the ComfyUI server can see.

Resolution order for the ComfyUI input directory:

1. ``COMFYUI_INPUT_DIR`` - an explicit override;
2. ``COMFYUI_PROD_DIR`` - the portable root, whose ``ComfyUI/input`` is the
   directory the server serves (this is what ``scripts/local_paths.py`` uses);
3. a drive scan for a portable root, as a last resort.

The directory is only needed when a chunk is actually rendered, so a broken
ComfyUI setup never stops ``--plan``.
"""
from __future__ import annotations

import glob
import os
import shutil
import sys
from pathlib import Path

__all__ = [
    "REPO_ROOT",
    "RUNNER",
    "scratch_dir",
    "comfy_input_dir",
    "comfy_output_dir",
    "python_exe",
    "ffmpeg_exe",
    "ffprobe_exe",
]

# <repo>/scripts/h3_chain_pipeline/paths.py -> <repo>
REPO_ROOT = Path(__file__).resolve().parents[2]
# The one and only renderer. This package never re-implements it.
RUNNER = REPO_ROOT / "scripts" / "h3_i2v_run.py"


def scratch_dir() -> Path:
    """Directory for intermediate chunk renders and reports."""
    env = os.environ.get("HERMES_SCRATCH_DIR")
    if env:
        return Path(env)
    return Path.home() / "AppData" / "Local" / "hermes" / "cache" / "scratch"


def _prod_dir() -> Path | None:
    """The ComfyUI portable root, or None when it cannot be found."""
    env = os.environ.get("COMFYUI_PROD_DIR")
    if env:
        return Path(env)
    for letter in "CDEFGH":
        for pattern in ("ComfyUI_windows_portable_nvidia_cu126", "ComfyUI_windows_portable*"):
            for hit in glob.glob(f"{letter}:/{pattern}/ComfyUI_windows_portable"):
                return Path(hit)
    return None


def comfy_input_dir() -> Path:
    """Directory whose file names the ComfyUI ``LoadImage`` node accepts."""
    env = os.environ.get("COMFYUI_INPUT_DIR")
    if env:
        return Path(env)
    prod = _prod_dir()
    if prod is not None:
        return prod / "ComfyUI" / "input"
    raise RuntimeError(
        "ComfyUI input directory is unknown: set COMFYUI_PROD_DIR (the portable "
        "root) or COMFYUI_INPUT_DIR (the input directory itself)"
    )


def comfy_output_dir() -> Path:
    """Directory ComfyUI writes its renders into."""
    env = os.environ.get("COMFYUI_OUTPUT_DIR")
    if env:
        return Path(env)
    prod = _prod_dir()
    if prod is not None:
        return prod / "ComfyUI" / "output"
    raise RuntimeError(
        "ComfyUI output directory is unknown: set COMFYUI_PROD_DIR (the portable "
        "root) or COMFYUI_OUTPUT_DIR (the output directory itself)"
    )


def python_exe() -> str:
    """Interpreter used to launch the renderer.

    The repository virtualenv wins when it exists, because the renderer and
    the pipeline must share one interpreter. ``HERMES_PYTHON`` overrides both.
    """
    env = os.environ.get("HERMES_PYTHON")
    if env:
        return env
    venv = REPO_ROOT / ".venv"
    if os.name == "nt":
        candidate = venv / "Scripts" / "python.exe"
    else:
        candidate = venv / "bin" / "python"
    if candidate.exists():
        return str(candidate)
    return sys.executable


def ffmpeg_exe() -> str:
    """The ffmpeg binary; ``FFMPEG_BIN`` overrides the PATH lookup."""
    env = os.environ.get("FFMPEG_BIN")
    if env:
        return env
    found = shutil.which("ffmpeg")
    if not found:
        raise RuntimeError("ffmpeg is not available on PATH (set FFMPEG_BIN)")
    return found


def ffprobe_exe() -> str:
    """The ffprobe binary; ``FFPROBE_BIN`` overrides the PATH lookup."""
    env = os.environ.get("FFPROBE_BIN")
    if env:
        return env
    found = shutil.which("ffprobe")
    if not found:
        raise RuntimeError("ffprobe is not available on PATH (set FFPROBE_BIN)")
    return found
