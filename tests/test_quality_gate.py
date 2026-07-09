"""Tests for the ComfyUI production quality gate.

Synthetic OpenCV fixtures (3 approved, 2 defective) are generated under
``tests/fixtures/`` at collection time so the suite never depends on real
generation outputs and runs in well under 30s.  insightface inference is never
triggered (the aggregate detectors do not call the face model, and the one
landmark test stubs the model out), so no model download is required.
"""

from __future__ import annotations

import json
from pathlib import Path

import cv2
import numpy as np
import pytest

from scripts import anatomy_analyzer as aa
from scripts import quality_gate as qg
from scripts.anatomy_analyzer import AnatomyAnalyzer
from scripts.quality_gate import QualityGate, load_rules

FIXTURES = Path(__file__).parent / "fixtures"
SKIN_BGR = (130, 172, 225)  # a canonical flesh tone that matches the skin mask
DARK_BG = (30, 30, 30)


# --------------------------------------------------------------------------- #
# fixture image builders
# --------------------------------------------------------------------------- #
def _canvas(h: int = 512, w: int = 512, color=DARK_BG) -> np.ndarray:
    img = np.zeros((h, w, 3), np.uint8)
    img[:] = color
    return img


def _approved_standing() -> np.ndarray:
    img = _canvas()
    cv2.rectangle(img, (200, 150), (312, 380), SKIN_BGR, -1)  # torso
    cv2.rectangle(img, (150, 200), (190, 240), SKIN_BGR, -1)  # left hand
    cv2.rectangle(img, (322, 200), (362, 240), SKIN_BGR, -1)  # right hand
    return img


def _approved_kneel() -> np.ndarray:
    img = _canvas()
    cv2.rectangle(img, (210, 180), (300, 360), SKIN_BGR, -1)
    cv2.rectangle(img, (170, 260), (205, 300), SKIN_BGR, -1)
    return img


def _approved_shore() -> np.ndarray:
    img = _canvas()
    cv2.ellipse(img, (256, 260), (60, 130), 0, 0, 360, SKIN_BGR, -1)
    return img


def _defect_extra_limbs() -> np.ndarray:
    img = _canvas()
    for x in (50, 150, 250):  # three compact hand blobs
        cv2.rectangle(img, (x, 50), (x + 40, 90), SKIN_BGR, -1)
    for x in (50, 150, 250):  # three elongated arm blobs
        cv2.rectangle(img, (x, 200), (x + 30, 340), SKIN_BGR, -1)
    return img


def _defect_toy_roulette() -> np.ndarray:
    img = _canvas(color=(200, 200, 200))
    cv2.ellipse(img, (256, 420), (60, 30), 0, 0, 360, (180, 40, 150), -1)  # purple toy
    return img


IMAGE_BUILDERS = {
    "approved_standing.png": _approved_standing,
    "approved_kneel.png": _approved_kneel,
    "approved_shore.png": _approved_shore,
    "defect_extra_limbs.png": _defect_extra_limbs,
    "defect_toy_roulette.png": _defect_toy_roulette,
}


@pytest.fixture(scope="session", autouse=True)
def fixture_images():
    FIXTURES.mkdir(parents=True, exist_ok=True)
    paths = {}
    for name, builder in IMAGE_BUILDERS.items():
        path = FIXTURES / name
        cv2.imwrite(str(path), builder())
        paths[name] = path
    return paths


@pytest.fixture()
def rules():
    return load_rules()


@pytest.fixture()
def anatomical_rules(rules):
    return rules["anatomical"]


# --------------------------------------------------------------------------- #
# AnatomyAnalyzer unit tests
# --------------------------------------------------------------------------- #
def test_skin_regions_counts(anatomical_rules):
    analyzer = AnatomyAnalyzer(_approved_standing(), "standing_water", rules=anatomical_rules)
    stats = analyzer.count_skin_regions()
    assert stats["regions"] >= 1
    assert 0.0 < stats["total_area_ratio"] < 1.0


def test_detect_extra_limbs_positive(anatomical_rules):
    analyzer = AnatomyAnalyzer(_defect_extra_limbs(), "nude woman", rules=anatomical_rules)
    assert analyzer.detect_extra_limbs() is True
    assert analyzer.confidences["extra_limbs"] >= 0.6


def test_detect_extra_limbs_negative(anatomical_rules):
    analyzer = AnatomyAnalyzer(_approved_standing(), "standing_water", rules=anatomical_rules)
    assert analyzer.detect_extra_limbs() is False


def test_detect_fused_hands(anatomical_rules):
    img = _canvas()
    cv2.rectangle(img, (120, 120), (400, 400), SKIN_BGR, -1)  # >15% solid skin blob
    analyzer = AnatomyAnalyzer(img, "nude", rules=anatomical_rules)
    assert analyzer.detect_fused_hands() is True


def test_detect_fused_hands_negative(anatomical_rules):
    analyzer = AnatomyAnalyzer(_approved_standing(), "standing_water", rules=anatomical_rules)
    assert analyzer.detect_fused_hands() is False


def test_detect_bad_feet(anatomical_rules):
    img = _canvas()
    for i in range(6):  # six toe blobs in the lower band
        x = 60 + i * 60
        cv2.rectangle(img, (x, 470), (x + 25, 500), SKIN_BGR, -1)
    analyzer = AnatomyAnalyzer(img, "feet", rules=anatomical_rules)
    assert analyzer.detect_bad_feet() is True


def test_detect_asymmetric_breasts_nsfw(anatomical_rules):
    img = _canvas()
    cv2.ellipse(img, (180, 200), (50, 40), 0, 0, 360, SKIN_BGR, -1)  # large left
    cv2.ellipse(img, (340, 200), (20, 15), 0, 0, 360, SKIN_BGR, -1)  # small right
    analyzer = AnatomyAnalyzer(img, "nude topless woman", rules=anatomical_rules)
    assert analyzer.detect_asymmetric_breasts() is True


def test_asymmetric_breasts_skipped_when_sfw(anatomical_rules):
    img = _canvas()
    cv2.ellipse(img, (180, 200), (50, 40), 0, 0, 360, SKIN_BGR, -1)
    cv2.ellipse(img, (340, 200), (20, 15), 0, 0, 360, SKIN_BGR, -1)
    analyzer = AnatomyAnalyzer(img, "standing_water portrait", rules=anatomical_rules)
    assert analyzer.detect_asymmetric_breasts() is False


def test_detect_toy_color_roulette(anatomical_rules):
    analyzer = AnatomyAnalyzer(_defect_toy_roulette(), "toy scene nude", rules=anatomical_rules)
    result = analyzer.detect_toy_color_roulette()
    assert result["roulette_flag"] is True
    assert result["toy_color"] in {"purple", "pink"}


def test_toy_color_roulette_flesh_tone_ok(anatomical_rules):
    img = _canvas(color=(200, 200, 200))
    cv2.ellipse(img, (256, 420), (60, 30), 0, 0, 360, SKIN_BGR, -1)  # flesh-tone toy
    analyzer = AnatomyAnalyzer(img, "toy scene nude", rules=anatomical_rules)
    assert analyzer.detect_toy_color_roulette()["roulette_flag"] is False


def test_pose_consistency_mismatch(anatomical_rules):
    img = _canvas()
    cv2.rectangle(img, (60, 220), (460, 300), SKIN_BGR, -1)  # wide -> lying
    analyzer = AnatomyAnalyzer(img, "standing_water", rules=anatomical_rules)
    assert analyzer.verify_pose_consistency("standing_water") is False


def test_pose_consistency_ok(anatomical_rules):
    img = _canvas()
    cv2.rectangle(img, (220, 60), (300, 460), SKIN_BGR, -1)  # tall -> standing
    analyzer = AnatomyAnalyzer(img, "standing_water", rules=anatomical_rules)
    assert analyzer.verify_pose_consistency("standing_water") is True


def test_face_landmarks_without_model(monkeypatch, anatomical_rules):
    monkeypatch.setattr(aa, "_get_face_app", lambda: None)
    analyzer = AnatomyAnalyzer(_approved_standing(), "portrait", rules=anatomical_rules)
    assert analyzer.detect_face_landmarks() == []


# --------------------------------------------------------------------------- #
# preflight tests
# --------------------------------------------------------------------------- #
def _sample_workflow(ckpt="flux-2-klein-9b.safetensors", lora="wan22/style.safetensors"):
    return {
        "1": {"class_type": "CheckpointLoaderSimple", "inputs": {"ckpt_name": ckpt}},
        "2": {"class_type": "LoraLoaderModelOnly", "inputs": {"lora_name": lora, "model": ["1", 0]}},
        "3": {"class_type": "VAELoader", "inputs": {"vae_name": "sdxl_vae.safetensors"}},
        "4": {
            "class_type": "KSampler",
            "inputs": {"seed": 42, "model": ["1", 0], "steps": 20},
        },
        "5": {"class_type": "SaveImage", "inputs": {"images": ["4", 0]}},
    }


def test_preflight_offline_missing_files(tmp_path):
    gate = QualityGate(models_roots=[tmp_path], comfyui_url="http://127.0.0.1:59999")
    report = gate.preflight(_sample_workflow())
    assert report.ok is False
    assert "flux-2-klein-9b.safetensors" in report.missing_files
    assert report.source == "filesystem"
    assert report.vram_estimate_gb > 0


def test_preflight_files_present(tmp_path):
    ckpt_dir = tmp_path / "checkpoints"
    lora_dir = tmp_path / "loras" / "wan22"
    vae_dir = tmp_path / "vae"
    for d in (ckpt_dir, lora_dir, vae_dir):
        d.mkdir(parents=True, exist_ok=True)
    (ckpt_dir / "flux-2-klein-9b.safetensors").write_bytes(b"x")
    (lora_dir / "style.safetensors").write_bytes(b"x")
    (vae_dir / "sdxl_vae.safetensors").write_bytes(b"x")

    gate = QualityGate(models_roots=[tmp_path], comfyui_url="http://127.0.0.1:59999")
    report = gate.preflight(_sample_workflow())
    assert report.ok is True
    assert report.missing_files == []


def test_preflight_vram_oom_high(tmp_path):
    wf = {
        "1": {"class_type": "UNETLoader", "inputs": {"unet_name": "wan2.2_14B.safetensors"}},
        "2": {"class_type": "KSampler", "inputs": {"seed": 1, "model": ["1", 0]}},
    }
    gate = QualityGate(models_roots=[tmp_path], comfyui_url="http://127.0.0.1:59999")
    report = gate.preflight(wf)
    assert report.oom_risk == "high"  # 12 + 2 VAE + 1.5 KSampler > 10


def test_preflight_vram_low(tmp_path):
    wf = {
        "1": {"class_type": "CheckpointLoaderSimple", "inputs": {"ckpt_name": "sd1.5-pruned.ckpt"}},
        "2": {"class_type": "KSampler", "inputs": {"seed": 1, "model": ["1", 0]}},
    }
    gate = QualityGate(models_roots=[tmp_path], comfyui_url="http://127.0.0.1:59999")
    report = gate.preflight(wf)
    assert report.oom_risk == "low"  # 2 + 2 + 1.5 = 5.5


def test_preflight_ui_graph_format(tmp_path):
    wf = {
        "nodes": [
            {
                "id": 1,
                "type": "CheckpointLoaderSimple",
                "widgets_values": ["ponyDiffusionV6XL.safetensors"],
            },
            {"id": 2, "type": "KSampler", "widgets_values": [123, 20, 7.0]},
        ]
    }
    gate = QualityGate(models_roots=[tmp_path], comfyui_url="http://127.0.0.1:59999")
    report = gate.preflight(wf)
    assert "ponyDiffusionV6XL.safetensors" in report.missing_files
    # pony -> 7 GB + 2 VAE + 1.5 KSampler = 10.5 -> high
    assert report.oom_risk == "high"


def test_preflight_non_json_file_raises(tmp_path):
    script = tmp_path / "camera_motion_stable.py"
    script.write_text("import os\nprint('hi')\n", encoding="utf-8")
    gate = QualityGate(models_roots=[tmp_path])
    with pytest.raises(ValueError):
        gate.preflight(script)


def test_preflight_missing_file_raises(tmp_path):
    gate = QualityGate(models_roots=[tmp_path])
    with pytest.raises(FileNotFoundError):
        gate.preflight(tmp_path / "does_not_exist.json")


def test_preflight_cli_on_script_does_not_crash(tmp_path, capsys):
    script = tmp_path / "camera_motion_stable.py"
    script.write_text("print('not a workflow')\n", encoding="utf-8")
    exit_code = qg.main(["preflight", str(script)])
    out = capsys.readouterr().out
    assert exit_code == 1
    payload = json.loads(out)
    assert payload["ok"] is False
    assert "error" in payload


# --------------------------------------------------------------------------- #
# postflight tests
# --------------------------------------------------------------------------- #
def test_postflight_approved(fixture_images):
    gate = QualityGate(comfyui_url="http://127.0.0.1:59999")
    report = gate.postflight(fixture_images["approved_standing.png"], "standing_water blonde")
    assert report.verdict == "approve"
    assert all(not d["detected"] for d in report.defects)


def test_postflight_defect_detected(fixture_images):
    gate = QualityGate(comfyui_url="http://127.0.0.1:59999")
    report = gate.postflight(fixture_images["defect_extra_limbs.png"], "nude beach editorial blonde")
    assert report.verdict in {"retry", "reject"}
    detected = [d for d in report.defects if d["detected"]]
    assert detected
    assert report.suggestions


def test_postflight_toy_roulette_defect(fixture_images):
    gate = QualityGate(comfyui_url="http://127.0.0.1:59999")
    report = gate.postflight(fixture_images["defect_toy_roulette.png"], "nude toy editorial")
    types = {d["type"] for d in report.defects if d["detected"]}
    assert "toy_color_roulette" in types


def test_postflight_forbidden_pose_rejects(fixture_images):
    gate = QualityGate(comfyui_url="http://127.0.0.1:59999")
    report = gate.postflight(
        fixture_images["approved_standing.png"], "deep squat with toy nude"
    )
    assert report.verdict == "reject"
    assert any(d["type"] == "pose_forbidden" for d in report.defects)


def test_postflight_telegram_oversize(tmp_path):
    big = tmp_path / "huge.png"
    big.write_bytes(b"\x00" * (11 * 1024 * 1024))  # 11 MB, invalid image
    gate = QualityGate(comfyui_url="http://127.0.0.1:59999")
    report = gate.postflight(big, "nude beach editorial")
    types = {d["type"] for d in report.defects if d["detected"]}
    assert "telegram_oversize" in types
    assert report.verdict == "reject"


def test_postflight_missing_image_graceful(tmp_path):
    gate = QualityGate(comfyui_url="http://127.0.0.1:59999")
    report = gate.postflight(tmp_path / "nope.png", "standing_water")
    assert report.verdict in {"approve", "retry", "reject"}
    assert any(d["type"] == "analysis_unavailable" for d in report.defects)


# --------------------------------------------------------------------------- #
# auto_retry tests
# --------------------------------------------------------------------------- #
def test_auto_retry_squat_toy_rejects_immediately(fixture_images):
    gate = QualityGate(comfyui_url="http://127.0.0.1:59999")
    wf = _sample_workflow()
    result = gate.auto_retry(
        fixture_images["approved_standing.png"],
        "deep squat toy nude",
        wf,
        max_attempts=3,
    )
    assert result.final_verdict == "reject"
    assert result.attempts == 1  # did not loop
    assert result.suggested_seed is None
    assert result.suggested_prompt_adjustments


def test_auto_retry_reject_suggests_seed(fixture_images):
    gate = QualityGate(comfyui_url="http://127.0.0.1:59999")
    wf = _sample_workflow()
    result = gate.auto_retry(
        fixture_images["defect_extra_limbs.png"],
        "nude beach editorial blonde",
        wf,
        max_attempts=3,
    )
    assert result.final_verdict == "reject"
    assert result.attempts <= 3
    assert result.suggested_seed is not None
    # workflow copy is mutated, not the caller's original
    assert wf["4"]["inputs"]["seed"] == 42


def test_auto_retry_approved_no_retry(fixture_images):
    gate = QualityGate(comfyui_url="http://127.0.0.1:59999")
    result = gate.auto_retry(
        fixture_images["approved_standing.png"],
        "standing_water blonde",
        _sample_workflow(),
        max_attempts=3,
    )
    assert result.final_verdict == "approve"
    assert result.attempts == 1


# --------------------------------------------------------------------------- #
# batch CLI
# --------------------------------------------------------------------------- #
def test_batch_postflight_cli(fixture_images, capsys):
    exit_code = qg.main(
        ["--comfy-url", "http://127.0.0.1:59999", "batch", "postflight", "--dir", str(FIXTURES)]
    )
    out = capsys.readouterr().out
    payload = json.loads(out)
    assert payload["processed"] == len(IMAGE_BUILDERS)
    assert set(payload["tally"]) == {"approve", "retry", "reject"}
    assert exit_code in {0, 1}
