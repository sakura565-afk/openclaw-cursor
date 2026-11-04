"""CPU resizers, existing pore injectors, and explicit external backends."""

from __future__ import annotations

import importlib.util
import os
from pathlib import Path
import subprocess
import sys

import cv2
import numpy as np

from scripts.pore_injector.field import build_pore_field
from scripts.pore_injector.inject import structure_map
from scripts.pore_injector.mask import fallback_skin_mask
from scripts.pore_injector_kimi.pore_injector import (
    build_hsv_skin_mask,
    generate_pore_field,
    local_structure_amplitude,
)

_REPOSITORY_ROOT = Path(__file__).resolve().parents[2]


def resize_rgb(image: np.ndarray, width: int, height: int, method: str) -> np.ndarray:
    interpolations = {
        "bicubic": cv2.INTER_CUBIC,
        "lanczos": cv2.INTER_LANCZOS4,
        "canonical": cv2.INTER_AREA if image.shape[1] > width else cv2.INTER_LANCZOS4,
    }
    if method not in interpolations:
        raise ValueError(f"unknown resize method: {method}")
    return cv2.resize(image, (width, height), interpolation=interpolations[method])


def apply_minimax(image: np.ndarray, seed: int = 1234) -> np.ndarray:
    """Apply the MiniMax injector's still equivalent at its video defaults."""
    frame = image.astype(np.float32) / 255.0
    field, _ = build_pore_field(
        frame.shape[:2],
        seed=seed,
        pit_density=260.0,
        rim=0.45,
        pore_sigma=1.1,
        two_octave=True,
    )
    mask, _ = fallback_skin_mask(frame)
    structure = structure_map(
        frame,
        mask=mask,
        detail_sigma=1.2,
        window_sigma=3.0,
        threshold=0.55,
        reference="local",
    )
    amount = structure * mask * 0.07
    output = np.clip(frame + (field * amount)[:, :, None], 0.0, 1.0)
    return np.rint(output * 255.0).astype(np.uint8)


def apply_kimi(image: np.ndarray, seed: int = 0) -> np.ndarray:
    """Apply the Kimi injector's still equivalent at its video defaults."""
    frame_bgr = cv2.cvtColor(image, cv2.COLOR_RGB2BGR)
    field = generate_pore_field(
        frame_bgr.shape[:2],
        pit_density=0.003,
        rim=0.35,
        pore_sigma=1.25,
        seed=seed,
        two_octave=True,
    )
    mask = build_hsv_skin_mask(frame_bgr).astype(np.float32) / 255.0
    amplitude = local_structure_amplitude(frame_bgr, threshold=0.008)
    injection = 2.0 * 255.0 * field * amplitude * mask
    output = np.clip(frame_bgr.astype(np.float32) + injection[..., None], 0.0, 255.0)
    return cv2.cvtColor(output.astype(np.uint8), cv2.COLOR_BGR2RGB)


def learned_backend_reason(candidate: str) -> str:
    """Explain why a learned backend has no measured row; never invent output."""
    modules = {"real_esrgan": "realesrgan", "codeformer": "codeformer"}
    module = modules[candidate]
    if importlib.util.find_spec(module) is None:
        return f"unavailable offline: Python package '{module}' is not installed"
    return (
        "unavailable in this CPU/offline run: no cached output supplied and "
        "no network download or GPU render was attempted"
    )


def run_seedvr2(source: Path, output: Path, plan: dict) -> None:
    """Invoke the repository's existing SeedVR2 path only when explicitly asked."""
    script = _REPOSITORY_ROOT / "scripts" / "upscale_video.py"
    timeout = float(os.environ.get("STILL_DETAIL_SEEDVR2_TIMEOUT", "14400"))
    command = [
        sys.executable,
        str(script),
        "--in", str(source),
        "--out", str(output),
        "--backend", "seedvr2",
        "--width", str(plan["target_width"]),
        "--height", str(plan["target_height"]),
        "--height-provided",
    ]
    result = subprocess.run(command, capture_output=True, text=True, timeout=timeout)
    backend_line = "backend: seedvr2" in result.stdout
    if result.returncode or not output.is_file() or not backend_line:
        detail = (result.stderr or result.stdout).strip()[-600:]
        raise RuntimeError(
            "SeedVR2 did not produce a verified seedvr2 backend output: " + detail
        )
