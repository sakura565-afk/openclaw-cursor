"""Shared, machine-independent path resolution for the local runners.

Every path here is overridable through the environment, and every default is
derived from the running machine (home directory, drive scan) instead of a
hardcoded drive-letter path. Hardcoded workstation paths break CI on other
hosts — the guard test `tests/test_no_absolute_paths.py` rejects them.

Environment variables:
  HERMES_SCRATCH_DIR   scratch dir for generated images/prompts
  COMFYUI_PROD_DIR     ComfyUI portable root (the dir that contains ComfyUI/)
  COMFYUI_LAB_DIR      ComfyUI LAB portable root (optional)
  HERMES_NATIVE_INPUTS dir with the original user-supplied inputs
  OPENCLAW_REPO_DIR    checkout root (defaults to this file's parents[1])
"""
from __future__ import annotations

import glob
import os
from pathlib import Path

__all__ = [
    "REPO_ROOT",
    "SCRATCH",
    "PROD_DIR",
    "PROD_IN",
    "PROD_OUT",
    "NATIVE_INPUTS",
]


def _scratch_dir() -> Path:
    env = os.environ.get("HERMES_SCRATCH_DIR")
    if env:
        return Path(env)
    return Path.home() / "AppData" / "Local" / "hermes" / "cache" / "scratch"


def _find_portable(*name_globs: str) -> Path | None:
    """Find a ComfyUI portable root by globbing the drive roots."""
    for letter in "CDEFGH":
        for pattern in name_globs:
            for hit in glob.glob(f"{letter}:/{pattern}/ComfyUI_windows_portable"):
                return Path(hit)
    return None


def _prod_dir() -> Path:
    env = os.environ.get("COMFYUI_PROD_DIR")
    if env:
        return Path(env)
    found = _find_portable(
        "ComfyUI_windows_portable_nvidia_cu126",
        "ComfyUI_windows_portable*",
    )
    if found:
        return found
    raise SystemExit(
        "COMFYUI_PROD_DIR is not set and no ComfyUI portable was found on drives C-H"
    )


SCRATCH: Path = _scratch_dir()
PROD_DIR: Path = _prod_dir()
PROD_IN: Path = PROD_DIR / "ComfyUI" / "input"
PROD_OUT: Path = PROD_DIR / "ComfyUI" / "output"
NATIVE_INPUTS: Path = Path(
    os.environ.get("HERMES_NATIVE_INPUTS")
    or (Path.home() / "AppData" / "Local" / "hermes" / "cache" / "documents" / "native-inputs")
)
REPO_ROOT: Path = Path(
    os.environ.get("OPENCLAW_REPO_DIR") or Path(__file__).resolve().parents[1]
)
