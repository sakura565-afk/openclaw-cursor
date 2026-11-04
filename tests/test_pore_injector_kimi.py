"""Tests for the pore-shaped, temporally stable injection field."""

from __future__ import annotations

import json
import math
import sys
from pathlib import Path

import cv2
import numpy as np
import pytest

SCRIPTS_DIR = Path(__file__).resolve().parents[1] / "scripts"
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

from pore_injector_kimi.cli import build_parser
from pore_injector_kimi.pore_injector import (
    build_hsv_skin_mask,
    generate_pore_field,
    local_structure_amplitude,
    process_video,
)


def test_pore_field_is_deterministic_sparse_and_has_dark_pits_with_rims() -> None:
    kwargs = dict(
        shape=(96, 128),
        pit_density=0.01,
        rim=0.35,
        pore_sigma=1.4,
        seed=17,
        two_octave=False,
    )

    field = generate_pore_field(**kwargs)
    repeated = generate_pore_field(**kwargs)

    assert field.shape == (96, 128)
    assert field.dtype == np.float32
    np.testing.assert_array_equal(field, repeated)
    assert float(field.min()) < -0.5
    assert float(field.max()) > 0.0
    assert np.count_nonzero(field == 0.0) > field.size // 5

    # The area around a dark centre must be brighter: this is a pit profile,
    # not zero-mean isotropic pixel noise.
    y, x = np.unravel_index(int(field.argmin()), field.shape)
    yy, xx = np.ogrid[: field.shape[0], : field.shape[1]]
    distance = np.sqrt((yy - y) ** 2 + (xx - x) ** 2)
    surrounding = (distance >= 2.5) & (distance <= 4.5)
    assert float(field[surrounding].mean()) > float(field[y, x])


def test_hsv_mask_rejects_foam_and_keeps_only_largest_component() -> None:
    frame = np.full((100, 140, 3), (20, 20, 20), dtype=np.uint8)
    skin_bgr = (70, 130, 190)
    cv2.rectangle(frame, (5, 5), (80, 90), skin_bgr, thickness=-1)
    cv2.rectangle(frame, (105, 10), (125, 35), skin_bgr, thickness=-1)
    cv2.circle(frame, (35, 35), 6, (255, 255, 255), thickness=-1)
    cv2.circle(frame, (60, 60), 6, (200, 200, 200), thickness=-1)
    frame[25, 50] = (255, 255, 255)
    frame[50, 25] = (200, 200, 200)

    mask = build_hsv_skin_mask(frame)

    assert mask.dtype == np.uint8
    assert mask.shape == frame.shape[:2]
    assert mask[20, 20] > 0
    assert mask[20, 115] == 0, "the smaller skin component must be discarded"
    assert mask[35, 35] == 0, "v > 0.95 foam must be rejected"
    assert mask[60, 60] == 0, "s < 0.10 foam must be rejected"
    assert mask[25, 50] == 0, "morphology must not restore a bright foam pixel"
    assert mask[50, 25] == 0, "morphology must not restore a grey foam pixel"
    assert 0.0 < float(np.count_nonzero(mask)) / mask.size < 0.6


def test_structure_amplitude_has_a_real_cutoff_and_no_floor() -> None:
    frame = np.full((96, 128, 3), 120, dtype=np.uint8)
    rng = np.random.default_rng(8)
    frame[:, 64:] = rng.integers(80, 180, size=(96, 64, 3), dtype=np.uint8)

    amplitude = local_structure_amplitude(frame, threshold=0.01)

    assert amplitude.dtype == np.float32
    assert amplitude.shape == frame.shape[:2]
    assert np.all(amplitude >= 0.0)
    assert np.count_nonzero(amplitude[10:-10, 10:50]) == 0
    assert float(amplitude[10:-10, 75:-10].mean()) > 0.0
    assert np.count_nonzero(local_structure_amplitude(frame, threshold=1.0)) == 0


def _write_test_video(path: Path, frames: int = 8) -> None:
    writer = cv2.VideoWriter(
        str(path), cv2.VideoWriter_fourcc(*"mp4v"), 12.0, (128, 96)
    )
    assert writer.isOpened()
    rng = np.random.default_rng(91)
    base = np.full((96, 128, 3), (25, 30, 35), dtype=np.uint8)
    noise = rng.integers(-30, 31, size=(76, 76, 3), dtype=np.int16)
    skin = np.clip(np.array((80, 140, 190)) + noise, 0, 255).astype(np.uint8)
    base[10:86, 10:86] = skin
    for index in range(frames):
        transform = np.float32([[1, 0, index % 3], [0, 1, 0]])
        frame = cv2.warpAffine(base, transform, (128, 96), borderMode=cv2.BORDER_REFLECT)
        writer.write(frame)
    writer.release()


def _decoded_frames(path: Path) -> list[np.ndarray]:
    capture = cv2.VideoCapture(str(path))
    frames = []
    while True:
        ok, frame = capture.read()
        if not ok:
            break
        frames.append(frame)
    capture.release()
    return frames


def test_process_video_writes_mp4_and_measures_the_encoded_result(tmp_path: Path) -> None:
    source = tmp_path / "source.mp4"
    output = tmp_path / "processed.mp4"
    mask_path = tmp_path / "mask.png"
    _write_test_video(source)
    assert cv2.imwrite(str(mask_path), np.full((96, 128), 255, dtype=np.uint8))

    report = process_video(
        video=source,
        out=output,
        strength=4.0,
        pit_density=0.004,
        rim=0.35,
        pore_sigma=1.3,
        threshold=0.005,
        mask=mask_path,
        temporal_sigma=1.5,
        seed=23,
        two_octave=True,
        max_frames=6,
    )

    assert output.is_file() and output.stat().st_size > 0
    assert report["frames_processed"] == 6
    assert report["mask_coverage_pct"] == 100.0
    assert report["pore_band_energy"]["before"] > 0.0
    assert report["pore_band_energy"]["after"] > 0.0
    assert math.isfinite(report["pore_band_energy"]["delta_pct"])
    assert math.isfinite(report["temporal_rgb_delta"]["delta_pct"])
    assert math.isfinite(report["temporal_hf_delta"]["delta_pct"])

    decoded = _decoded_frames(output)
    assert len(decoded) == 6
    expected_rgb_delta = float(
        np.mean(
            [np.abs(decoded[i].astype(np.float32) - decoded[i - 1]).mean()
             for i in range(1, len(decoded))]
        )
    )
    assert report["temporal_rgb_delta"]["after"] == pytest.approx(expected_rgb_delta)

    report_path = output.with_suffix(".json")
    assert json.loads(report_path.read_text(encoding="utf-8")) == report


def test_cli_exposes_the_complete_brief_contract() -> None:
    args = build_parser().parse_args([
        "--video", "input.mp4",
        "--out", "output.mp4",
        "--strength", "2.5",
        "--pit-density", "0.004",
        "--rim", "0.4",
        "--pore-sigma", "1.2",
        "--threshold", "0.01",
        "--mask", "skin.png",
        "--temporal-sigma", "1.5",
        "--seed", "42",
        "--two-octave",
        "--max-frames", "12",
    ])

    assert args.video == "input.mp4"
    assert args.out == "output.mp4"
    assert args.strength == 2.5
    assert args.pit_density == 0.004
    assert args.rim == 0.4
    assert args.pore_sigma == 1.2
    assert args.threshold == 0.01
    assert args.mask == "skin.png"
    assert args.temporal_sigma == 1.5
    assert args.seed == 42
    assert args.two_octave is True
    assert args.max_frames == 12
