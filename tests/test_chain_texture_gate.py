"""Contracts for survival-based long-chain texture acceptance."""

from __future__ import annotations

import numpy as np
import pytest
from PIL import Image

from scripts.chain_texture_gate import (
    GateConfig,
    evaluate_metrics,
    format_chunk_report,
    make_chunk_report,
    measure_added_texture,
)
from scripts.chain_texture_gate.__main__ import main
from scripts.eval_judge.metrics import gaussian_blur
from scripts.skin_texture_metrics import materials_root

FULL_ROI = {"full": (0.0, 0.0, 1.0, 1.0)}


def _smooth_base(size: int = 192) -> np.ndarray:
    y, x = np.mgrid[:size, :size]
    return 120.0 + 20.0 * y / (size - 1) + 10.0 * x / (size - 1)


def _coherent_detail(size: int = 192) -> np.ndarray:
    impulses = np.zeros((size, size), dtype=np.float64)
    impulses[6::12, 6::12] = 800.0
    return gaussian_blur(impulses, 2.5)


def _measured(before: np.ndarray, after: np.ndarray) -> dict:
    metrics = measure_added_texture(before, [after], rois=FULL_ROI)
    metrics.update(face_drift=0.0, face_drift_tail=0.0, tail_frames=1)
    return metrics


def test_coherent_detail_passes_but_sand_is_rejected() -> None:
    before = _smooth_base()
    rng = np.random.default_rng(19)
    detail_metrics = _measured(before, before + _coherent_detail())
    sand_metrics = _measured(before, before + rng.normal(0.0, 12.0, before.shape))

    detail = evaluate_metrics(detail_metrics)
    sand = evaluate_metrics(sand_metrics)

    assert detail.accepted, detail.reasons
    assert not sand.accepted
    assert any("survival" in reason for reason in sand.reasons)
    assert sand_metrics["pore"] > 0.0
    assert sand_metrics["structure_score"] < detail_metrics["structure_score"]


def test_bc_style_finished_structure_does_not_hide_noise_like_delta() -> None:
    # Measured BC_ab_f0_10 values: the finished image alone looks acceptable.
    metrics = {
        "face_drift": 0.0,
        "face_drift_tail": 0.0,
        "tail_frames": 1,
        "frames": 1,
        "pore": 5.937e10,
        "survival": 0.226,
        "texture_survival": 0.773,
        "structure_score": 0.214,
    }

    result = evaluate_metrics(metrics)

    assert metrics["structure_score"] >= GateConfig().min_structure
    assert not result.accepted
    assert any("added-band survival" in reason for reason in result.reasons)


def test_baseline_ratchet_allows_small_variation_but_rejects_degradation() -> None:
    baseline = {"survival": 0.80, "structure_score": 0.50}
    common = {
        "face_drift": 0.0,
        "face_drift_tail": 0.0,
        "tail_frames": 1,
        "frames": 2,
        "pore": 100.0,
    }

    acceptable = evaluate_metrics(
        {**common, "survival": 0.70, "structure_score": 0.41}, baseline=baseline
    )
    low_survival = evaluate_metrics(
        {**common, "survival": 0.67, "structure_score": 0.41}, baseline=baseline
    )
    low_structure = evaluate_metrics(
        {**common, "survival": 0.70, "structure_score": 0.39}, baseline=baseline
    )

    assert acceptable.accepted, acceptable.reasons
    assert not low_survival.accepted
    assert not low_structure.accepted
    assert any("baseline" in reason for reason in low_survival.reasons)
    assert any("baseline" in reason for reason in low_structure.reasons)


def test_gate_fails_closed_when_texture_measurements_are_missing() -> None:
    result = evaluate_metrics(
        {"face_drift": 0.0, "face_drift_tail": 0.0, "tail_frames": 1, "frames": 2, "pore": 1.0}
    )

    assert not result.accepted
    assert any("survival" in reason for reason in result.reasons)
    assert any("structure" in reason for reason in result.reasons)


def test_chunk_report_contains_all_decision_fields() -> None:
    metrics = _measured(_smooth_base(), _smooth_base() + _coherent_detail())
    result = evaluate_metrics(metrics)

    report = make_chunk_report("chunk_000", metrics, result)
    table = format_chunk_report(report)

    assert report["accepted"] is True
    assert report["reasons"] == []
    for key in (
        "face_drift",
        "face_drift_tail",
        "pore",
        "survival",
        "texture_survival",
        "structure_score",
    ):
        assert key in report
        assert key in table
    assert "ACCEPT" in table


def test_plan_command_reuses_existing_pipeline_without_rendering(capsys: pytest.CaptureFixture[str]) -> None:
    exit_code = main([
        "--image", "portrait.png",
        "--prompt-file", "prompt.txt",
        "--audio", "track.wav",
        "--output", "long.mp4",
        "--total-frames", "166",
        "--chunk-frames", "56",
        "--plan",
    ])

    captured = capsys.readouterr()
    assert exit_code == 0
    assert "3 chunks" in captured.out
    assert "PLAN ONLY" in captured.out


def test_real_bc_delta_is_measured_and_rejected() -> None:
    root = materials_root()
    before_path = root / "ab_f0.png"
    after_path = root / "BST_ab_f0_10.png"
    if not before_path.exists() or not after_path.exists():
        pytest.skip("real BC before/after images are absent")
    before = np.asarray(Image.open(before_path).convert("RGB"), dtype=np.float64)
    after = np.asarray(Image.open(after_path).convert("RGB"), dtype=np.float64)

    metrics = measure_added_texture(before, [after])
    metrics.update(face_drift=0.0, face_drift_tail=0.0, tail_frames=1)
    result = evaluate_metrics(metrics)

    assert metrics["structure_score"] >= GateConfig().min_structure
    assert metrics["survival"] == pytest.approx(0.226, abs=0.01)
    assert not result.accepted
    assert any("added-band survival" in reason for reason in result.reasons)


def test_offline_table_exposes_chunk_chain_and_seam_retention() -> None:
    from scripts.chain_texture_gate.offline import format_offline_table

    row = {
        "pore": 1.0e10,
        "pore_rms": 5.0,
        "survival": 0.75,
        "structure_score": 0.25,
        "frames": 12,
    }
    report = {
        "rows": [
            {"label": "single_chunk", **row},
            {"label": "assembled", **row},
            {"label": "seam_before", **row},
            {"label": "seam_after", **row},
        ],
        "retention": {
            "assembled_vs_single_survival": 1.0,
            "assembled_vs_single_structure": 1.0,
            "seam_after_vs_before_survival": 0.98,
            "seam_after_vs_before_structure": 0.97,
        },
    }

    table = format_offline_table(report)

    assert "single_chunk" in table
    assert "assembled" in table
    assert "seam_before" in table
    assert "survival" in table
    assert "structure" in table
    assert "98.0%" in table


def test_real_existing_single_and_chained_videos_have_an_offline_seam_report(
    tmp_path,
) -> None:
    from scripts.chain_texture_gate.offline import compare_existing

    root = materials_root()
    single = root / "MASTURB2_5s.mp4"
    assembled = root / "MASTURB2_V6.mp4"
    if not single.exists() or not assembled.exists():
        pytest.skip("existing single/chained MASTURB2 artifacts are absent")

    report = compare_existing(
        single, assembled, boundary=120, work=tmp_path, frames=3, seam_frames=2
    )

    assert [row["label"] for row in report["rows"][:2]] == ["single_chunk", "assembled"]
    assert report["boundary"] == 120
    assert report["retention"]["assembled_vs_single_survival"] > 0.0
    assert report["retention"]["seam_after_vs_before_structure"] > 0.0
    assert report["seam_texture"]["survival"] == pytest.approx(0.563, abs=0.01)
    assert report["seam_texture"]["accepted"] is True


def test_pipeline_retries_only_failed_chunk_and_uses_first_accepted_baseline(
    tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from scripts.chain_texture_gate.runner import TextureChainPipeline
    from scripts.h3_chain_pipeline_kimi.pipeline import ChainConfig

    config = ChainConfig(
        image="portrait.png",
        prompt_file=tmp_path / "prompt.txt",
        audio=tmp_path / "track.wav",
        output=tmp_path / "long.mp4",
        chunk_frames=56,
        total_frames=111,
        width=736,
        height=1120,
        steps=20,
        seed_base=42,
        max_retries=1,
        max_face_drift=8.0,
        min_texture=0.0,
        max_texture_drop=0.0,
        state=tmp_path / "state.json",
        server="",
        identity_lora="none",
        tail_frames=5,
    )
    pipeline = TextureChainPipeline(config)
    pipeline.state = pipeline._new_state("test-fingerprint")
    pipeline.job_id = "test"

    def fake_render(**kwargs) -> Path:
        output = kwargs["output"]
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_bytes(b"fake video")
        return output

    def fake_measure(video: Path, attempt_dir: Path, anchor: Path) -> dict:
        metrics = {
            "face_drift": 1.0,
            "face_drift_tail": 1.5,
            "tail_frames": 5,
            "frames": 24,
            "pore": 100.0,
            "survival": 0.80,
            "texture_survival": 0.85,
            "structure_score": 0.50,
        }
        if video.stem == "chunk_1_0":
            metrics["survival"] = 0.20
        elif video.stem == "chunk_1_1":
            metrics.update(survival=0.70, structure_score=0.41)
        return metrics

    def fake_last_frame(video: Path, output: Path) -> Path:
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_bytes(b"anchor")
        return output

    monkeypatch.setattr(pipeline, "_render_attempt", fake_render)
    monkeypatch.setattr(pipeline, "_measure_chunk", fake_measure)
    monkeypatch.setattr(pipeline, "_extract_last_frame", fake_last_frame)

    pipeline._process_chunk(0, "anchor_000.png")
    pipeline._process_chunk(1, "anchor_001.png")

    first, second = pipeline.state["chunks"]
    assert first["gate_passed"] is True
    assert len(first["attempts"]) == 1
    assert second["gate_passed"] is True
    assert [attempt["seed"] for attempt in second["attempts"]] == [1_000_042, 1_000_043]
    assert second["attempts"][0]["accepted"] is False
    assert second["attempts"][1]["baseline_chunk"] == 0
    assert second["texture_survival_floor"] == pytest.approx(0.68)
    assert second["texture_structure_floor"] == pytest.approx(0.40)
