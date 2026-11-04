"""Tests for the blind A/B skin-texture judge."""

from __future__ import annotations

import json
from pathlib import Path
import subprocess
import sys

import cv2
import numpy as np
import pytest
from PIL import Image

from scripts.eval_judge_kimi.eval_judge import (
    aggregate_scores,
    flicker_guard,
    make_blind_sheet,
    select_skin_crops,
    skin_mask,
)


def test_skin_mask_accepts_uint8_and_finds_skin() -> None:
    frame = np.zeros((48, 64, 3), dtype=np.uint8)
    frame[8:40, 12:52] = (220, 160, 130)

    mask = skin_mask(frame)

    assert mask.dtype == np.uint8
    assert mask.shape == frame.shape[:2]
    assert mask[20, 30] == 255
    assert mask[0, 0] == 0


def test_empty_skin_mask_is_rejected_before_crop_selection() -> None:
    with pytest.raises(ValueError, match="skin mask is empty"):
        select_skin_crops(np.zeros((48, 64, 3), dtype=np.uint8), count=1)


def test_select_skin_crops_returns_requested_canonical_crops() -> None:
    frame = np.zeros((128, 160, 3), dtype=np.uint8)
    frame[:, 20:140] = (220, 160, 130)

    crops = select_skin_crops(frame, count=2, canonical_size=64)

    assert len(crops) == 2
    assert all(crop.shape == (64, 64, 3) for crop in crops)
    assert all(crop.dtype == np.uint8 for crop in crops)
    assert all(np.any(skin_mask(crop)) for crop in crops)


def test_blind_sheet_uses_shuffled_letter_labels(tmp_path: Path) -> None:
    crops = {
        "secret_reference.mp4": [np.full((32, 32, 3), 100, dtype=np.uint8)],
        "secret_candidate.mp4": [np.full((32, 32, 3), 180, dtype=np.uint8)],
    }
    output = tmp_path / "blind_sheet.png"

    key = make_blind_sheet(crops, output, seed=7)

    assert set(key) == {"A", "B"}
    assert set(key.values()) == set(crops)
    assert output.is_file()
    with Image.open(output) as sheet:
        assert sheet.width > sheet.height
        assert sheet.getpixel((44, 40)) == (20, 20, 20)
    assert "secret_reference" not in output.read_bytes().decode("latin-1", errors="ignore")


def test_flicker_guard_rejects_fps_and_motion_mismatches() -> None:
    assert flicker_guard(24.0, 24.0, 3.0, 3.2) is None
    assert "fps" in flicker_guard(24.0, 48.0, 3.0, 3.0)
    assert "motion" in flicker_guard(24.0, 24.0, 1.0, 4.0)
    assert "motion" in flicker_guard(24.0, 24.0, 0.0, 2.0)


def test_external_scores_have_median_iqr_and_verdict() -> None:
    scores = {
        "A": [
            {"pore_readability": 8, "naturalness": 8, "wax_plastic": 2, "overlaid_noise": 2},
            {"pore_readability": 6, "naturalness": 8, "wax_plastic": 4, "overlaid_noise": 2},
        ],
        "B": [
            {"pore_readability": 4, "naturalness": 5, "wax_plastic": 7, "overlaid_noise": 8},
            {"pore_readability": 2, "naturalness": 3, "wax_plastic": 9, "overlaid_noise": 8},
        ],
    }

    result = aggregate_scores(scores)

    assert result["A"]["pore_readability"]["median"] == 7
    assert result["A"]["pore_readability"]["iqr"] == 1
    assert result["A"]["verdict"] == "pass"
    assert result["B"]["verdict"] == "fail"


def _write_skin_mp4(path: Path, fps: float) -> None:
    writer = cv2.VideoWriter(str(path), cv2.VideoWriter_fourcc(*"mp4v"), fps, (128, 96))
    assert writer.isOpened()
    for index in range(6):
        frame = np.full((96, 128, 3), (120, 150, 200), dtype=np.uint8)
        for x in range(12, 116, 16):
            cv2.circle(frame, (x, 30 + (index % 2)), 2, (90, 110, 160), -1)
        assert writer.write(frame)
    writer.release()
    assert path.stat().st_size > 0


def test_cli_processes_mp4_and_marks_fps_mismatch_not_comparable(tmp_path: Path) -> None:
    reference = tmp_path / "reference.mp4"
    same = tmp_path / "same.mp4"
    fast = tmp_path / "fast.mp4"
    _write_skin_mp4(reference, 12.0)
    _write_skin_mp4(same, 12.0)
    _write_skin_mp4(fast, 24.0)
    output = tmp_path / "result"

    completed = subprocess.run(
        [
            sys.executable,
            "-m",
            "scripts.eval_judge_kimi",
            "--reference",
            str(reference),
            "--candidates",
            str(same),
            str(fast),
            "--frames",
            "3",
            "--crops",
            "2",
            "--out",
            str(output),
            "--seed",
            "5",
            "--dump-prompt",
        ],
        check=True,
        capture_output=True,
        text=True,
        timeout=120,
    )

    report = json.loads((output / "report.json").read_text(encoding="utf-8"))
    assert (output / "blind_sheet.png").stat().st_size > 0
    assert report["candidates"][0]["flicker"]["status"] == "comparable"
    assert report["candidates"][1]["flicker"] == {
        "status": "not comparable",
        "reason": "fps differs: reference=12.000, candidate=24.000",
    }
    assert "not comparable" in completed.stdout
    assert "pore_readability" in completed.stdout
    assert "Variant" in completed.stdout
