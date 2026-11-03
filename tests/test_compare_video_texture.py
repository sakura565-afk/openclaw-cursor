"""Tests for the texture/flicker A/B tool.

Two traps this closes, both of which produced plausible-looking wrong numbers
before they were caught:

- the pore band is defined in PIXELS, so a 736-wide clip and a 1072-wide clip
  are not comparable unless every ROI is resampled to the same scale;
- the ROI tuple order is (x0, y0, x1, y1), and getting it wrong silently
  produces an empty crop and a NaN measurement.
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import numpy as np
import pytest
from PIL import Image

from scripts.compare_video_texture import canonical_sizes, measure


def _ffmpeg() -> str:
    exe = shutil.which("ffmpeg")
    if not exe:
        pytest.skip("ffmpeg is not available")
    return exe


def _make_clip(path: Path, width: int, height: int, frames: int = 6) -> Path:
    """A clip with visible per-pixel texture, so the pore band has content."""
    subprocess.run(
        [_ffmpeg(), "-y", "-v", "error", "-f", "lavfi",
         "-i", f"testsrc=size={width}x{height}:rate=24:duration={frames / 24:.2f}",
         "-c:v", "libx264", "-crf", "16", "-pix_fmt", "yuv420p", str(path)],
        check=True, timeout=300)
    return path


def _frames(clip: Path, out_dir: Path) -> list[str]:
    out_dir.mkdir(parents=True, exist_ok=True)
    subprocess.run(
        [_ffmpeg(), "-y", "-v", "error", "-i", str(clip), "-frames:v", "6",
         "-fps_mode", "passthrough", str(out_dir / "f_%03d.png")],
        check=True, timeout=300)
    return sorted(str(p) for p in out_dir.glob("f_*.png"))


def test_roi_order_is_x0_y0_x1_y1(tmp_path: Path) -> None:
    """A wrong tuple order gives an empty crop, not a wrong number."""
    img = np.zeros((200, 100, 3), dtype=np.uint8)
    path = tmp_path / "f.png"
    Image.fromarray(img).save(path)

    from scripts.probe_pore_spectrum import roi_slice

    y0, y1, x0, x1 = roi_slice(img.shape[:2], (0.1, 0.2, 0.5, 0.6))
    assert x0 < x1 and y0 < y1
    assert (x0, x1) == (10, 50)
    assert (y0, y1) == (40, 120)


def test_canonical_sizes_returns_height_and_width(tmp_path: Path) -> None:
    path = tmp_path / "f.png"
    Image.fromarray(np.zeros((200, 100, 3), dtype=np.uint8)).save(path)

    sizes = canonical_sizes([str(path)])

    assert sizes["thigh"] == (int(0.821 * 200) - int(0.606 * 200),
                               int(0.469 * 100) - int(0.170 * 100))
    assert all(h > 0 and w > 0 for h, w in sizes.values())


def test_a_scaled_clip_measures_the_same_at_canonical_scale(tmp_path: Path) -> None:
    """The whole point of the canonical scale: resolution must not move the number."""
    small = _make_clip(tmp_path / "small.mp4", 320, 240)
    big = _make_clip(tmp_path / "big.mp4", 640, 480)

    small_frames = _frames(small, tmp_path / "s")
    big_frames = _frames(big, tmp_path / "b")

    canonical = canonical_sizes(small_frames)
    a = measure(small_frames, canonical=canonical)
    b = measure(big_frames, canonical=canonical)

    assert a["pore"] > 0
    # without canonicalisation the big clip reads several times higher,
    # because the same physical texture covers more pixels
    assert b["pore"] == pytest.approx(a["pore"], rel=0.35)


def test_measure_reports_temporal_metrics(tmp_path: Path) -> None:
    clip = _make_clip(tmp_path / "c.mp4", 320, 240, frames=6)
    frames = _frames(clip, tmp_path / "f")

    result = measure(frames, canonical=canonical_sizes(frames))

    assert result["frames"] == 6
    assert result["rgb_delta"] > 0
    assert result["hf_delta"] > 0
    assert result["pore"] > 0
