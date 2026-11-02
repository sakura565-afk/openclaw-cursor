"""Tests for objective temporal motion QC."""

from __future__ import annotations

import shutil
import subprocess
import sys
import json
from pathlib import Path

import numpy as np
import pytest
from PIL import Image

from scripts.video_motion_qc import analyze_frames, analyze_video


def test_static_frames_are_frozen_and_fail() -> None:
    frames = [np.full((48, 64), 90, dtype=np.uint8) for _ in range(5)]

    report = analyze_frames(frames)

    assert report["global_energy"] == 0.0
    assert report["motion_energy"] == 0.0
    assert report["freeze_score"] == 1.0
    assert report["temporal_std"] == 0.0
    assert report["drift_vs_first"] == 0.0
    assert report["verdict"] == "FAIL"


def test_static_roi_fails_even_when_rest_of_frame_moves() -> None:
    frames = []
    for index in range(5):
        frame = np.full((64, 64), 30, dtype=np.uint8)
        frame[32:, :] = 30 + index * 10
        frames.append(frame)

    report = analyze_frames(frames, roi=(0, 0, 32, 32))

    assert report["global_energy"] > 0.0
    assert report["roi_energy"] == 0.0
    assert report["motion_energy"] == 0.0
    assert report["freeze_score"] == 1.0
    assert report["verdict"] == "FAIL"


def _moving_square_frames(shift: int = 2) -> list[np.ndarray]:
    frames = []
    tile = np.arange(256, dtype=np.uint8).reshape(16, 16)
    for index in range(6):
        frame = np.zeros((64, 80), dtype=np.uint8)
        x = 8 + index * shift
        frame[24:40, x : x + 16] = tile
        frames.append(frame)
    return frames


def test_block_matching_recovers_direction_of_moving_texture() -> None:
    report = analyze_frames(_moving_square_frames())

    assert 0.5 < report["motion_vector"][0] <= 2.0
    assert abs(report["motion_vector"][1]) < 0.1
    assert report["motion_magnitude"] > 0.5
    assert report["mean_block_magnitude"] > 0.5
    assert report["verdict"] == "PASS"


def test_pose_jump_has_higher_temporal_std_and_drift_than_steady_motion() -> None:
    steady = [np.full((32, 32), 10 + index, dtype=np.uint8) for index in range(6)]
    pose_jump = [np.zeros((32, 32), dtype=np.uint8) for _ in range(3)]
    pose_jump += [np.full((32, 32), 80, dtype=np.uint8) for _ in range(3)]

    steady_report = analyze_frames(steady, threshold=0.1)
    jump_report = analyze_frames(pose_jump, threshold=0.1)

    assert jump_report["temporal_std"] > steady_report["temporal_std"]
    assert jump_report["drift_vs_first"] > 0.0
    assert steady_report["drift_vs_first"] > 0.0
    assert steady_report["verdict"] == "PASS"


def test_freeze_threshold_is_configurable() -> None:
    frames = [np.full((32, 32), index % 2, dtype=np.uint8) for index in range(5)]

    strict = analyze_frames(frames, threshold=2.0)
    sensitive = analyze_frames(frames, threshold=0.5)

    assert strict["freeze_score"] == 1.0
    assert strict["verdict"] == "FAIL"
    assert sensitive["freeze_score"] == 0.0
    assert sensitive["verdict"] == "PASS"


def test_warn_and_fail_freeze_thresholds_are_configurable() -> None:
    frames = [np.full((32, 32), value, dtype=np.uint8) for value in (0, 0, 10, 10, 20)]

    default = analyze_frames(frames)
    strict = analyze_frames(frames, freeze_warn=0.4, freeze_fail=0.4)
    permissive = analyze_frames(frames, freeze_warn=0.75, freeze_fail=0.9)

    assert default["freeze_score"] == 0.5
    assert default["verdict"] == "WARN"
    assert strict["verdict"] == "FAIL"
    assert permissive["verdict"] == "PASS"


def test_mask_limits_energy_and_block_motion_to_selected_region() -> None:
    frames = []
    for index in range(5):
        frame = np.zeros((64, 64), dtype=np.uint8)
        frame[:, 32:] = index * 20
        frame[16:32, 40:56] = np.arange(256, dtype=np.uint8).reshape(16, 16)
        frames.append(frame)
    mask = np.zeros((64, 64), dtype=bool)
    mask[:, :32] = True

    report = analyze_frames(frames, mask=mask)

    assert report["global_energy"] > 0.0
    assert report["motion_energy"] == 0.0
    assert report["freeze_score"] == 1.0
    assert report["motion_vector"] == [0.0, 0.0]
    assert report["verdict"] == "FAIL"


def _render_lavfi(source: str, output: Path) -> None:
    proc = subprocess.run(
        [
            "ffmpeg",
            "-y",
            "-v",
            "error",
            "-f",
            "lavfi",
            "-i",
            source,
            "-c:v",
            "libx264",
            "-preset",
            "ultrafast",
            "-crf",
            "18",
            "-pix_fmt",
            "yuv420p",
            str(output),
        ],
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )
    if proc.returncode != 0:
        pytest.skip(f"ffmpeg lavfi is unavailable: {proc.stderr.strip()}")


@pytest.mark.skipif(
    shutil.which("ffmpeg") is None or shutil.which("ffprobe") is None,
    reason="ffmpeg/ffprobe not installed",
)
def test_static_lavfi_clip_has_high_freeze_score_and_fails(tmp_path: Path) -> None:
    video = tmp_path / "static.mp4"
    _render_lavfi("color=c=gray:s=64x64:r=8:d=1", video)

    report = analyze_video(video)

    assert report["frame_count"] == 8
    assert report["freeze_score"] >= 0.99
    assert report["global_energy"] < 0.1
    assert report["verdict"] == "FAIL"


@pytest.mark.skipif(
    shutil.which("ffmpeg") is None or shutil.which("ffprobe") is None,
    reason="ffmpeg/ffprobe not installed",
)
def test_moving_lavfi_clip_passes(tmp_path: Path) -> None:
    video = tmp_path / "moving.mp4"
    _render_lavfi("testsrc2=s=96x80:r=8:d=1", video)

    report = analyze_video(video, threshold=1.0)

    assert report["frame_count"] == 8
    assert report["global_energy"] > 1.0
    assert report["freeze_score"] < 0.5
    assert report["mean_block_magnitude"] > 0.1
    assert report["verdict"] == "PASS"


@pytest.mark.skipif(
    shutil.which("ffmpeg") is None or shutil.which("ffprobe") is None,
    reason="ffmpeg/ffprobe not installed",
)
def test_cli_writes_complete_valid_json(tmp_path: Path) -> None:
    video = tmp_path / "cli.mp4"
    output = tmp_path / "qc.json"
    mask = tmp_path / "mask.png"
    Image.new("L", (96, 80), 255).save(mask)
    _render_lavfi("testsrc2=s=96x80:r=8:d=1", video)
    script = Path(__file__).resolve().parents[1] / "scripts" / "video_motion_qc.py"

    proc = subprocess.run(
        [
            sys.executable,
            str(script),
            str(video),
            "--roi",
            "0,0,96,80",
            "--mask",
            str(mask),
            "--threshold",
            "1.0",
            "--json",
            str(output),
        ],
        capture_output=True,
        text=True,
        timeout=120,
        check=False,
    )

    assert proc.returncode == 0, proc.stderr
    report = json.loads(output.read_text(encoding="utf-8"))
    assert json.loads(proc.stdout) == report
    assert {
        "global_energy",
        "roi_energy",
        "motion_energy",
        "freeze_score",
        "temporal_std",
        "drift_vs_first",
        "motion_vector",
        "motion_magnitude",
        "verdict",
    } <= report.keys()
    assert report["roi"] == {"x": 0, "y": 0, "w": 96, "h": 80}
    assert report["verdict"] == "PASS"
