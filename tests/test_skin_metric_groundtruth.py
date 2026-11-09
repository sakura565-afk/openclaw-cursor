"""Independent synthetic ground truth for the skin-texture metric."""

from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pytest
from PIL import Image

from scripts.skin_metric_groundtruth.blind import build_blind_set
from scripts.skin_metric_groundtruth.calibration import build_calibration_report
from scripts.skin_metric_groundtruth.synthetic import (
    build_cases,
    generate_dataset,
)
from scripts.skin_metric_shape import SHAPE_MAX_COMPONENT_DENSITY
from scripts.skin_texture_metrics import measure_image


def test_required_core_cases_land_in_their_known_classes() -> None:
    cases = [case for case in build_cases() if case.core]

    assert {case.family for case in cases} == {"plastic", "pores", "noise", "mixed"}
    assert len(cases) == 4
    for case in cases:
        result = measure_image(case.image)
        assert result["classification"] == case.expected_class, case.case_id

    by_family = {case.family: measure_image(case.image) for case in cases}
    assert by_family["pores"]["survival"] > by_family["noise"]["survival"]
    assert by_family["pores"]["pore_rms"] > by_family["plastic"]["pore_rms"]


def test_generator_diagnostics_have_known_pore_count_size_and_density() -> None:
    cases = build_cases()
    fine = [case for case in cases if case.family == "fine_pores"]
    coarse = [case for case in cases if case.family == "pores"]

    assert len(fine) == 4
    assert len(coarse) == 4
    for case in fine + coarse:
        stats = case.parameters["pore_field"]
        assert stats["pit_count"] > 0
        assert stats["actual_pit_density_per_10k"] > 0
        assert case.parameters["pore_sigma_px"] > 0
        assert case.parameters["pore_fwhm_diameter_px"] > 0


def test_dataset_preserves_signed_truth_components_and_relative_paths(
    tmp_path: Path,
) -> None:
    entries = generate_dataset(tmp_path, size=96)
    manifest_path = tmp_path / "synthetic_manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))

    assert len(entries) == 20
    assert len(manifest["cases"]) == 20
    for entry in entries:
        assert not Path(entry["image"]).is_absolute()
        image_path = tmp_path / entry["image"]
        component_path = tmp_path / entry["components"]
        saved = np.asarray(Image.open(image_path).convert("L"), dtype=np.float64)
        with np.load(component_path) as components:
            reconstructed = np.clip(
                entry["parameters"]["base_level"]
                + components["pore_delta"]
                + components["noise_delta"],
                0.0,
                255.0,
            )
        assert np.array_equal(saved, np.rint(reconstructed).astype(np.uint8))


def test_blind_sheet_has_only_numbered_crops_and_a_separate_key(
    tmp_path: Path,
) -> None:
    generate_dataset(tmp_path, size=96)
    result = build_blind_set(tmp_path / "synthetic_manifest.json", tmp_path / "blind", seed=7)
    prompt = (tmp_path / "blind" / "judge_prompt.txt").read_text(encoding="utf-8")
    key = json.loads((tmp_path / "blind" / "blind_key.json").read_text(encoding="utf-8"))

    assert (tmp_path / "blind" / "blind_sheet.png").stat().st_size > 0
    assert len(key["crops"]) == 20
    assert [crop["number"] for crop in key["crops"]] == list(range(1, 21))
    assert [crop["case_id"] for crop in key["crops"]] != [
        entry["case_id"] for entry in json.loads(
            (tmp_path / "synthetic_manifest.json").read_text(encoding="utf-8")
        )["cases"]
    ]
    assert "Answer with JSON only" in prompt
    assert '"1": {"class": "plastic"' in prompt
    assert all(case_id not in prompt for case_id in [crop["case_id"] for crop in key["crops"]])
    assert result["visual_status"] == "pending_external_judge"


def test_committed_artifacts_score_20_of_20_constructive_truth() -> None:
    root = Path(__file__).resolve().parents[1] / "scripts" / "skin_metric_groundtruth" / "artifacts"
    report = build_calibration_report(
        root / "synthetic_manifest.json",
        blind_key_path=root / "blind" / "blind_key.json",
        visual_labels_path=root / "blind" / "human_labels.json",
    )

    assert report["summary"]["core_truth_matches"] == 4
    assert report["summary"]["truth_matches"] == 20
    assert report["summary"]["truth_mismatches"] == []
    rec = report["recommended_thresholds"]
    assert rec["basis"]["fine_pore_truth_mismatches"] == []
    assert len(rec["basis"]["legacy_fine_pore_truth_mismatches"]) == 4
    assert rec["shape_max_component_density"] == pytest.approx(SHAPE_MAX_COMPONENT_DENSITY)
    assert rec["basis"]["component_density_separation_ratio"] > 1.0
    assert rec["basis"]["fine_pore_and_mixed_survival_ranges_overlap"] is True


def test_calibration_reports_core_truth_and_blind_visual_agreements(
    tmp_path: Path,
) -> None:
    entries = generate_dataset(tmp_path)
    build_blind_set(tmp_path / "synthetic_manifest.json", tmp_path / "blind", seed=7)
    key = json.loads((tmp_path / "blind" / "blind_key.json").read_text(encoding="utf-8"))
    expected_by_id = {entry["case_id"]: entry["expected_class"] for entry in entries}
    labels = {
        "judge": "test-double, not a human",
        "labels": {
            str(crop["number"]): {
                "class": expected_by_id[crop["case_id"]],
                "confidence": "test",
            }
            for crop in key["crops"]
        },
    }
    labels_path = tmp_path / "blind" / "visual_labels.json"
    labels_path.write_text(json.dumps(labels), encoding="utf-8")

    report = build_calibration_report(
        tmp_path / "synthetic_manifest.json",
        blind_key_path=tmp_path / "blind" / "blind_key.json",
        visual_labels_path=labels_path,
    )
    report_path = tmp_path / "calibration_report.json"
    report_path.write_text(json.dumps(report), encoding="utf-8")

    assert report["summary"]["core_total"] == 4
    assert report["summary"]["core_truth_matches"] == 4
    assert report["summary"]["visual_total"] == 20
    assert report["summary"]["visual_metric_matches"] == 20
    assert len(report["summary"]["visual_metric_mismatches"]) == 0
    assert report["recommended_thresholds"]["status"] == "provisional_pending_human_review"
    assert "human" in report["recommended_thresholds"]["caveat"]
    assert all("classification" in row["metrics"] for row in report["rows"])
    assert report_path.is_file()
