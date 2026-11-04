"""Shape/sparsity regression tests for the skin-metric blind spot."""

from __future__ import annotations

import json
import os
from pathlib import Path

import numpy as np
import pytest

from scripts import compare_video_texture
from scripts.skin_metric_groundtruth.synthetic import build_cases
from scripts.skin_metric_shape import (
    SHAPE_MAX_COMPONENT_DENSITY,
    measure_shape,
    measure_shape_sequence,
)
from scripts.skin_metric_shape.calibration import build_blind_report
from scripts.skin_texture_metrics import measure_frames, measure_image


REPO_ROOT = Path(__file__).resolve().parents[1]
BLIND_ROOT = REPO_ROOT / "scripts" / "skin_metric_groundtruth" / "artifacts" / "blind"


def test_component_density_separates_all_constructive_pores_from_grain() -> None:
    rows = []
    for case in build_cases():
        if case.expected_class == "plastic":
            continue
        measured = measure_shape(case.image)
        rows.append((case.expected_class, measured["component_density_per_10k"]))

    pore_max = max(value for label, value in rows if label == "pores")
    sand_min = min(value for label, value in rows if label == "digital_sand")
    assert pore_max < SHAPE_MAX_COMPONENT_DENSITY < sand_min
    assert sand_min / pore_max > 3.0


def test_blind_report_matches_human_and_constructive_truth_20_of_20() -> None:
    report = build_blind_report(BLIND_ROOT)

    assert report["summary"]["human_is_ground_truth"] is True
    assert report["summary"]["old_vs_human"] == 16
    assert report["summary"]["new_vs_human"] == 20
    assert report["summary"]["new_vs_truth"] == 20
    assert report["summary"]["truth_total"] == 20
    assert report["summary"]["corrected_crops"] == [10, 11, 18, 20]
    assert all(row["truth"] == row["human"] == row["metric_new"] for row in report["rows"])


def test_shape_threshold_is_calibrated_from_core_controls_not_fine_failures() -> None:
    report = build_blind_report(BLIND_ROOT)
    basis = report["calibration"]["basis"]

    assert basis["core_control_count"] == 4
    assert basis["core_pore_component_density_max"] < basis["core_sand_component_density_min"]
    assert report["calibration"]["shape_max_component_density"] == pytest.approx(
        (basis["core_pore_component_density_max"] + basis["core_sand_component_density_min"]) / 2.0
    )
    assert report["candidate_comparison"]["selected"] == "component_density_per_10k"


def test_frame_mode_compares_neighbours_and_rewards_persistent_structure() -> None:
    pore_case = next(case for case in build_cases() if case.family == "fine_pores")
    pore_frames = [pore_case.image, pore_case.image.copy(), pore_case.image.copy()]
    rng = np.random.default_rng(8128)
    noise_frames = [rng.normal(128.0, 16.0, pore_case.image.shape) for _ in range(3)]

    pores = measure_shape_sequence(pore_frames)
    noise = measure_shape_sequence(noise_frames)

    assert pores["frames"] == 3
    assert len(pores["neighbor_highpass_correlations"]) == 2
    assert pores["neighbor_highpass_correlations"] == pytest.approx([1.0, 1.0])
    assert pores["temporal_component_stability"] == pytest.approx(1.0)
    assert np.mean(pores["neighbor_highpass_correlations"]) > np.mean(
        noise["neighbor_highpass_correlations"]
    )
    assert 0.0 <= noise["temporal_component_stability"] <= 1.0


def test_measure_frames_exposes_temporal_shape_without_changing_still_api(tmp_path: Path) -> None:
    case = next(case for case in build_cases() if case.family == "fine_pores")
    paths = []
    from PIL import Image

    for index in range(3):
        path = tmp_path / f"frame_{index}.png"
        Image.fromarray(case.image).save(path)
        paths.append(path)

    still = measure_image(paths[0])
    video = measure_frames(paths, rois={"full": (0.0, 0.0, 1.0, 1.0)})

    assert still["classification"] == "pores"
    assert still["shape_component_density_per_10k"] > 0.0
    assert video["classification"] == "pores"
    assert video["temporal_shape"]["neighbor_comparisons"] == 2
    assert video["temporal_shape"]["mean_highpass_correlation"] == pytest.approx(1.0)


def test_real_decoded_chef_frame_uses_the_same_shape_pipeline(tmp_path: Path) -> None:
    configured = os.environ.get("HERMES_RENDER_ROOT", "").strip()
    if not configured:
        pytest.skip("real render root is not configured")
    video_path = Path(configured) / "CHEF_ONE_REF2VA.mp4"
    if not video_path.exists():
        pytest.skip("CHEF_ONE_REF2VA is unavailable")

    paths = compare_video_texture.extract(str(video_path), tmp_path / "frames", frames=3)
    canonical = compare_video_texture.canonical_sizes(paths)
    measured = measure_frames(paths, canonical=canonical)

    assert measured["classification"] == "pores"
    assert measured["shape_component_density_per_10k"] < SHAPE_MAX_COMPONENT_DENSITY
    assert measured["temporal_shape"]["neighbor_comparisons"] > 0
