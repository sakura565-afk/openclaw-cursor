"""Synthetic contracts and real, eye-labelled references for skin texture metrics."""

from __future__ import annotations

import json
import os
from pathlib import Path

import numpy as np
import pytest
from PIL import Image

from scripts import compare_video_texture
from scripts.eval_judge.metrics import gaussian_blur
from scripts.skin_texture_metrics import (
    classify,
    format_table,
    is_pore_like,
    measure_frames,
    measure_image,
    measure_patch,
)
from scripts.skin_texture_metrics.cli import main as cli_main


def _materials_root() -> Path | None:
    for variable in ("HERMES_NATIVE_INPUTS", "HERMES_SCRATCH_DIR"):
        value = os.environ.get(variable, "").strip()
        if value:
            return Path(value)
    fallback = Path.home() / "AppData" / "Local" / "hermes" / "cache" / "scratch"
    return fallback if fallback.exists() else None


def _smooth_base(size: int = 192) -> np.ndarray:
    y, x = np.mgrid[:size, :size]
    return 120.0 + 20.0 * y / (size - 1) + 10.0 * x / (size - 1)


def _coherent_pores(size: int = 192) -> np.ndarray:
    """Separated, blur-surviving Gaussian bumps in the pore spatial band."""
    impulses = np.zeros((size, size), dtype=np.float64)
    impulses[6::12, 6::12] = 800.0
    return gaussian_blur(impulses, 2.5)


def test_patch_reports_band_energy_before_and_after_matched_blur() -> None:
    gray = _smooth_base() + _coherent_pores()

    result = measure_patch(gray)

    assert result["pore"] > 0.0
    assert result["pore_blur"] > 0.0
    assert result["survival"] == pytest.approx(result["pore_blur"] / result["pore"])
    assert 0.0 <= result["structure_score"] <= 1.0


def test_synthetic_pores_noise_and_plastic_have_distinct_classes() -> None:
    base = _smooth_base()
    rng = np.random.default_rng(7)
    plastic = measure_image(base)
    pores = measure_image(base + _coherent_pores())
    sand = measure_image(base + rng.normal(0.0, 12.0, base.shape))

    assert plastic["classification"] == "plastic"
    assert pores["classification"] == "pores"
    assert sand["classification"] == "digital_sand"
    assert pores["survival"] > sand["survival"]
    assert pores["structure_score"] > sand["structure_score"]
    assert sand["pore_rms"] > plastic["pore_rms"]


def test_is_pore_like_accepts_coherent_structure_but_rejects_added_noise() -> None:
    before = _smooth_base()
    rng = np.random.default_rng(11)

    pore_ok, pore_reason = is_pore_like(before, before + _coherent_pores())
    noise_ok, noise_reason = is_pore_like(before, before + rng.normal(0.0, 12.0, before.shape))

    assert pore_ok, pore_reason
    assert not noise_ok
    assert "survival" in noise_reason


def test_is_pore_like_rejects_identical_and_misaligned_inputs() -> None:
    before = _smooth_base()

    identical_ok, identical_reason = is_pore_like(before, before.copy())
    mismatch_ok, mismatch_reason = is_pore_like(before, before[:-2])

    assert not identical_ok
    assert "energy" in identical_reason
    assert not mismatch_ok
    assert "shape" in mismatch_reason


def test_classification_does_not_reward_energy_without_survival() -> None:
    assert classify({"pore_rms": 8.0, "survival": 0.1}) == "digital_sand"
    assert classify({"pore_rms": 8.0, "survival": 0.8}) == "pores"
    assert classify({"pore_rms": 0.1, "survival": 0.8}) == "plastic"


def test_table_exposes_the_discriminating_numbers() -> None:
    table = format_table([
        {"label": "plastic", "pore": 10.0, "pore_blur": 8.0, "pore_rms": 1.0,
         "survival": 0.8, "structure_score": 0.1, "lap": 2.0,
         "classification": "plastic"},
        {"label": "sand", "pore": 20.0, "pore_blur": 2.0, "pore_rms": 8.0,
         "survival": 0.1, "structure_score": 0.0, "lap": 40.0,
         "classification": "digital_sand"},
    ])

    assert "survival" in table
    assert "structure" in table
    assert "plastic" in table
    assert "digital_sand" in table


@pytest.fixture(scope="session")
def real_video_measurements(tmp_path_factory: pytest.TempPathFactory) -> dict[str, dict]:
    root = _materials_root()
    if root is None:
        pytest.skip("real skin-texture materials are not configured")
    videos = {
        "plastic": root / "chain_high" / "full_v2.mp4",
        "sand": root / "PORE_TIGHT_012.mp4",
        "flow": root / "PORE_FLOW_0.05.mp4",
    }
    missing = [path.name for path in videos.values() if not path.exists()]
    if missing:
        pytest.skip(f"real eye-labelled clips are absent: {missing}")
    work = tmp_path_factory.mktemp("skin_texture_real")
    extracted = {
        label: compare_video_texture.extract(str(path), work / label, frames=3)
        for label, path in videos.items()
    }
    canonical = compare_video_texture.canonical_sizes(extracted["plastic"])
    return {
        label: measure_frames(paths, canonical=canonical)
        for label, paths in extracted.items()
    }


def test_real_eye_labelled_clips_land_in_the_expected_three_regimes(
    real_video_measurements: dict[str, dict],
) -> None:
    plastic = real_video_measurements["plastic"]
    sand = real_video_measurements["sand"]
    flow = real_video_measurements["flow"]

    assert plastic["classification"] == "plastic"
    assert sand["classification"] == "digital_sand"
    assert flow["classification"] == "pores"
    assert sand["survival"] < flow["survival"] < plastic["survival"]
    assert plastic["pore_rms"] < flow["pore_rms"] < sand["pore_rms"]
    assert flow["structure_score"] > plastic["structure_score"]
    assert flow["structure_score"] > sand["structure_score"]


@pytest.mark.parametrize("level", ["10", "20", "30"])
def test_real_bc_skin_texture_delta_is_rejected_by_rollback_gate(level: str) -> None:
    root = _materials_root()
    if root is None:
        pytest.skip("real skin-texture materials are not configured")
    before_path = root / "ab_f0.png"
    after_path = root / f"BST_ab_f0_{level}.png"
    if not before_path.exists() or not after_path.exists():
        pytest.skip("real BC before/after images are absent")
    before = np.asarray(Image.open(before_path).convert("RGB"))
    after = np.asarray(Image.open(after_path).convert("RGB"))

    accepted, reason = is_pore_like(before, after)

    assert not accepted
    assert "survival" in reason


def test_real_set_cli_prints_table_and_rejects_bc(
    real_video_measurements: dict[str, dict],
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    json_path = tmp_path / "report.json"

    exit_code = cli_main([
        "--real-set", "--frames", "3", "--work", str(tmp_path / "frames"),
        "--json-out", str(json_path),
    ])

    captured = capsys.readouterr()
    assert exit_code == 0
    assert "plastic_full_v2" in captured.out
    assert "sand_PORE_TIGHT_012" in captured.out
    assert "flow_PORE_FLOW_0.05" in captured.out
    assert "digital_sand" in captured.out
    assert "gate BC_ab_f0_10: REJECT" in captured.out
    report = json.loads(json_path.read_text(encoding="utf-8"))
    assert report["videos"]["plastic_full_v2"]["classification"] == "plastic"
    assert report["videos"]["sand_PORE_TIGHT_012"]["classification"] == "digital_sand"
    assert report["gates"]["BC_ab_f0_10"]["accepted"] is False
