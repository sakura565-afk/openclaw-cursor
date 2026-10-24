"""Tests for scripts.banding_detector."""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import numpy as np
import pytest
from PIL import Image

from scripts.banding_detector import (
    analyze_frame_array,
    analyze_frames,
    analyze_paths,
    analyze_video,
)


def _gradient_frame(h: int = 256, w: int = 256) -> np.ndarray:
    y = np.linspace(0, 255, h, dtype=np.uint8)
    row = np.stack([y, y, y], axis=1)
    return np.repeat(row[:, np.newaxis, :], w, axis=1)


def _flat_frame(h: int = 256, w: int = 256, color: tuple[int, int, int] = (120, 130, 140)) -> np.ndarray:
    arr = np.zeros((h, w, 3), dtype=np.uint8)
    arr[:, :] = color
    return arr


def _banded_frame(
    h: int = 256,
    w: int = 256,
    y0: int = 100,
    height: int = 16,
    band_color: tuple[int, int, int] = (0, 70, 0),
) -> np.ndarray:
    base = _gradient_frame(h, w)
    y1 = y0 + height
    base[y0:y1, :, 0] = band_color[0]
    base[y0:y1, :, 1] = band_color[1]
    base[y0:y1, :, 2] = band_color[2]
    return base


class TestAnalyzeFrameArray:
    def test_clean_gradient(self) -> None:
        fb = analyze_frame_array(_gradient_frame())
        assert fb.band_count == 0

    def test_clean_flat(self) -> None:
        fb = analyze_frame_array(_flat_frame())
        assert fb.band_count == 0

    def test_detects_16px_green_band(self) -> None:
        fb = analyze_frame_array(_banded_frame(height=16))
        assert fb.band_count >= 1
        heights = [b[2] for b in fb.bands]
        assert any(14 <= h <= 18 for h in heights)


class TestAnalyzeFrames:
    def test_clean_scores_zero(self) -> None:
        frames = [_gradient_frame(), _flat_frame(), _gradient_frame()]
        report = analyze_frames(frames)
        assert report.verdict == "clean"
        assert report.band_count == 0
        assert report.score == 0.0

    def test_single_frame_band_not_counted(self) -> None:
        frames = [_flat_frame() for _ in range(5)]
        frames[2] = _banded_frame()
        report = analyze_frames(frames, min_consecutive_frames=2)
        assert report.band_count == 0
        assert report.verdict == "clean"
        assert report.score == 0.0

    def test_three_of_five_frames_counted(self) -> None:
        frames = [_flat_frame() for _ in range(5)]
        for idx in (1, 2, 4):
            frames[idx] = _banded_frame(y0=100, height=16)
        report = analyze_frames(frames, min_consecutive_frames=2)
        assert report.band_count >= 1
        assert report.affected_frame_ratio == 0.6
        assert report.verdict == "banding"

    def test_banded_scores_higher_than_clean(self) -> None:
        clean = analyze_frames([_gradient_frame() for _ in range(5)])
        banded_frames = [_banded_frame(y0=100, height=16) for _ in range(5)]
        banded = analyze_frames(banded_frames)
        assert clean.score < banded.score
        assert banded.score > 0.0


class TestAnalyzePaths:
    def test_image_list(self, tmp_path: Path) -> None:
        paths = []
        for i in range(3):
            p = tmp_path / f"f{i}.png"
            Image.fromarray(_flat_frame()).save(p)
            paths.append(p)
        report = analyze_paths([str(p) for p in paths])
        assert report["frames_analyzed"] == 3
        assert report["verdict"] == "clean"


@pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="ffmpeg not installed")
class TestAnalyzeVideoFfmpeg:
    def test_striped_lavfi_video(self, tmp_path: Path) -> None:
        video = tmp_path / "stripes.mp4"
        # 64px tall frame with a dark green horizontal bar in the middle
        vf = (
            "color=c=0x788088:s=320x64:d=1,"
            "drawbox=x=0:y=24:w=320:h=16:color=0x001400:t=fill"
        )
        proc = subprocess.run(
            [
                "ffmpeg",
                "-y",
                "-f",
                "lavfi",
                "-i",
                vf,
                "-t",
                "1",
                "-r",
                "5",
                str(video),
            ],
            capture_output=True,
            text=True,
        )
        if proc.returncode != 0:
            pytest.skip(f"ffmpeg lavfi failed: {proc.stderr.strip()}")

        report = analyze_video(video, fps=5.0, min_consecutive_frames=2)
        assert report["verdict"] == "banding"
        assert report["band_count"] >= 1
