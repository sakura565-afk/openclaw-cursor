"""Tests for the still-detail candidate evaluator."""

from __future__ import annotations

import json

import cv2
import numpy as np
import pytest

from scripts.still_detail_ceiling.candidates import apply_kimi, apply_minimax
from scripts.still_detail_ceiling.cli import main
from scripts.still_detail_ceiling.geometry import scaling_plan
from scripts.still_detail_ceiling.metrics import measure_texture, oversharpening_suspected
from scripts.still_detail_ceiling.review import choose_review_roi


def test_scaling_plan_preserves_aspect_within_rounding() -> None:
    plan = scaling_plan(576, 1024, target_width=1072)

    assert plan["requested_width"] == 1072
    assert plan["target_width"] == 1088
    assert plan["target_height"] == 1920
    assert plan["width_adjustment_px"] == 16
    assert plan["target_width"] % 32 == 0
    assert plan["target_height"] % 32 == 0
    assert plan["aspect_error_pct"] == pytest.approx(0.7352941, abs=1e-5)
    assert plan["violations"] == []


def test_scaling_plan_records_aspect_violation() -> None:
    plan = scaling_plan(576, 1024, target_width=1072, aspect_tolerance_pct=0.1)

    assert "aspect_ratio_error_exceeds_tolerance" in plan["violations"]


def test_scaling_plan_records_requested_width_alignment() -> None:
    plan = scaling_plan(64, 96, target_width=100)

    assert plan["requested_width"] == 100
    assert plan["target_width"] == 96
    assert plan["width_adjustment_px"] == -4
    assert plan["target_width"] % 32 == 0


def test_texture_metrics_use_surviving_energy_not_only_sharpness() -> None:
    image = np.zeros((96, 96), dtype=np.float32)
    image[48, 48] = 255.0

    metrics = measure_texture(image)

    assert metrics["pore_energy"] > 0.0
    assert 0.0 < metrics["pore_survival"] < 1.0
    assert 0.0 < metrics["hf_survival"] < 1.0
    assert metrics["surviving_pore_energy"] == pytest.approx(
        metrics["pore_energy"] * metrics["pore_survival"]
    )


def test_texture_metrics_are_finite_on_flat_image() -> None:
    metrics = measure_texture(np.full((64, 64), 127.0, dtype=np.float32))

    assert metrics["pore_energy"] == pytest.approx(0.0)
    assert metrics["pore_survival"] == 0.0
    assert metrics["hf_survival"] == 0.0


@pytest.mark.parametrize("injector", [apply_minimax, apply_kimi])
def test_injectors_are_deterministic_on_a_still(injector) -> None:
    rng = np.random.default_rng(7)
    image = np.empty((96, 64, 3), dtype=np.uint8)
    image[:] = (190, 125, 100)
    noise = rng.normal(0.0, 5.0, image.shape[:2])[..., None]
    image = np.clip(image.astype(np.float32) + noise, 0, 255).astype(np.uint8)

    first = injector(image, seed=42)
    second = injector(image, seed=42)

    assert first.shape == image.shape
    assert first.dtype == np.uint8
    assert np.array_equal(first, second)
    assert not np.array_equal(first, image)


def test_review_roi_is_fixed_size_and_inside_image() -> None:
    image = np.full((160, 128, 3), (190, 125, 100), dtype=np.uint8)

    roi = choose_review_roi(image, size=64)

    assert roi == (32, 48, 96, 112)


def test_oversharpening_requires_energy_and_survival_evidence() -> None:
    row = {
        "status": "ok",
        "laplacian_delta_pct_vs_lanczos": 13.5,
        "pore_survival_delta_pp_vs_lanczos": -4.2,
    }
    assert oversharpening_suspected(row)

    row["pore_survival_delta_pp_vs_lanczos"] = 1.0
    assert not oversharpening_suspected(row)


def test_cli_runs_offline_and_marks_unavailable_candidates(tmp_path, capsys) -> None:
    rng = np.random.default_rng(11)
    image = np.empty((96, 64, 3), dtype=np.uint8)
    image[:] = (190, 125, 100)
    noise = rng.normal(0.0, 4.0, image.shape[:2])[..., None]
    image = np.clip(image.astype(np.float32) + noise, 0, 255).astype(np.uint8)
    source = tmp_path / "style.png"
    assert cv2.imwrite(str(source), cv2.cvtColor(image, cv2.COLOR_RGB2BGR))

    code = main([
        "--image", str(source),
        "--output-dir", str(tmp_path / "out"),
        "--target-width", "64",
        "--review-size", "32",
        "--review-roi", "style=8,16,40,48",
    ])

    assert code == 0
    stdout = capsys.readouterr().out
    assert "bicubic" in stdout
    assert "pore_minimax" in stdout
    assert "unavailable" in stdout
    report = json.loads((tmp_path / "out" / "report.json").read_text(encoding="utf-8"))
    assert len(report["styles"]) == 1
    assert report["styles"][0]["review_roi_mode"] == "manual"
    assert "review_crop_warning" in report["styles"][0]["recommendation"]
    candidates = {row["candidate"]: row for row in report["styles"][0]["candidates"]}
    assert candidates["real_esrgan"]["status"] == "unavailable"
    assert candidates["codeformer"]["status"] == "unavailable"
    assert candidates["seedvr2"]["status"] == "unavailable"
    assert "metrics" not in candidates["real_esrgan"]
    assert all(
        "pore_gate" in row for row in candidates.values() if row["status"] == "ok"
    )
    assert (tmp_path / "out" / "style" / "review_sheet.png").is_file()
