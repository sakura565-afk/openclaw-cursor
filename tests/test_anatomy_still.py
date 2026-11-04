from __future__ import annotations

import json
from pathlib import Path

import cv2
import numpy as np
import pytest
from PIL import Image

from scripts.anatomy_still.metrics import validate_anatomy
from scripts.anatomy_still.pose_mask import PoseEstimate, build_mask_from_pose
from scripts.anatomy_still.recipes import get_recipe, load_recipes


def _ellipse_mask(width: int, height: int) -> np.ndarray:
    mask = np.zeros((height, width), dtype=np.uint8)
    cv2.ellipse(
        mask,
        (width // 2, int(height * 0.62)),
        (int(width * 0.16), int(height * 0.09)),
        0,
        0,
        360,
        255,
        -1,
    )
    return mask


def _smooth_skin(width: int = 240, height: int = 320) -> np.ndarray:
    rng = np.random.default_rng(7)
    base = np.array([183, 132, 112], dtype=np.float32)
    image = np.broadcast_to(base, (height, width, 3)).copy()
    image += rng.normal(0, 0.45, image.shape)
    return np.clip(image, 0, 255).astype(np.uint8)


def test_pose_mask_is_derived_from_hips_and_knees_at_chef_size() -> None:
    pose = PoseEstimate(
        left_hip=(0.35, 0.55),
        right_hip=(0.65, 0.55),
        left_knee=(0.32, 0.90),
        right_knee=(0.68, 0.90),
        visibility=0.99,
    )
    mask = build_mask_from_pose(
        (853, 1280), pose, center_shift=0.16, width_scale=1.20, height_scale=0.34
    )

    assert mask.shape == (1280, 853)
    assert mask.dtype == np.uint8
    assert set(np.unique(mask)).issubset({0, 255})
    ys, xs = np.nonzero(mask)
    assert abs(float(xs.mean()) / 853 - 0.50) < 0.01
    assert abs(float(ys.mean()) / 1280 - (0.55 + 0.16 * 0.35)) < 0.01
    assert 0.30 < (xs.max() - xs.min()) / 853 < 0.40


def test_pose_mask_scales_to_katya_size_and_clamps_at_frame_border() -> None:
    pose = PoseEstimate(
        left_hip=(0.04, 0.48),
        right_hip=(0.42, 0.50),
        left_knee=(0.02, 0.82),
        right_knee=(0.46, 0.84),
        visibility=0.95,
    )
    mask = build_mask_from_pose((810, 991), pose)
    assert mask.shape == (991, 810)
    assert np.count_nonzero(mask) > 1000
    assert np.count_nonzero(mask[0]) == 0
    assert np.count_nonzero(mask[:, -1]) == 0


def test_seated_pose_uses_shoulders_when_knees_are_level_with_hips() -> None:
    pose = PoseEstimate(
        left_hip=(0.50, 0.75),
        right_hip=(0.70, 0.75),
        left_knee=(0.16, 0.77),
        right_knee=(1.00, 0.73),
        left_shoulder=(0.48, 0.35),
        right_shoulder=(0.68, 0.35),
        visibility=0.99,
        hip_visibility=0.99,
    )
    mask = build_mask_from_pose((810, 991), pose, center_shift=0.13)
    ys, xs = np.nonzero(mask)
    assert 0.75 < ys.mean() / 991 < 0.82
    assert 0.56 < xs.mean() / 810 < 0.64


def test_pose_mask_rejects_invisible_hips() -> None:
    pose = PoseEstimate(
        left_hip=(0.35, 0.55),
        right_hip=(0.65, 0.55),
        left_knee=(0.32, 0.90),
        right_knee=(0.68, 0.90),
        visibility=0.20,
    )
    with pytest.raises(ValueError, match="visibility"):
        build_mask_from_pose((853, 1280), pose)


def test_validator_rejects_smooth_skin_as_anatomy() -> None:
    image = _smooth_skin()
    result = validate_anatomy(image, _ellipse_mask(240, 320))
    assert result.texture_score < 0.30
    assert result.cloth_score < 0.45
    assert 0.0 <= result.visibility < 0.25
    assert not result.passed


def test_validator_accepts_textured_non_fabric_region() -> None:
    image = _smooth_skin().astype(np.float32)
    mask = _ellipse_mask(240, 320)
    rng = np.random.default_rng(19)
    yy, xx = np.mgrid[:320, :240]
    detail = (
        4.0 * np.sin(xx / 3.1)
        + 3.0 * np.sin(yy / 4.3)
        + rng.normal(0, 3.0, (320, 240))
    )
    cleft = 30.0 * np.exp(-((xx - 120 - 3 * np.sin(yy / 17)) / 4.0) ** 2)
    fold = 9.0 * np.exp(-((np.abs(xx - 120) - 12) / 5.0) ** 2)
    selected = mask > 0
    image[selected, 0] += detail[selected] - cleft[selected] + fold[selected]
    image[selected, 1] += 0.65 * detail[selected] - 0.55 * cleft[selected]
    image[selected, 2] += 0.55 * detail[selected] - 0.45 * cleft[selected]
    result = validate_anatomy(np.clip(image, 0, 255).astype(np.uint8), mask)
    assert result.texture_score > 0.60
    assert result.cloth_score < 0.45
    assert result.visibility > 0.55
    assert result.passed


@pytest.mark.parametrize("color", [(238, 238, 232), (24, 22, 25)])
def test_validator_detects_uniform_fabric_with_structural_edges(color: tuple[int, int, int]) -> None:
    image = _smooth_skin()
    mask = _ellipse_mask(240, 320)
    yy, xx = np.mgrid[:320, :240]
    fabric = (np.abs(xx - 120) < 25) & (np.abs(yy - 198) < 18)
    image[fabric] = color
    # A coherent waistband edge is a fabric cue even when its colour is skin-like.
    image[172:176, 86:155] = color
    result = validate_anatomy(image, mask)
    assert result.uniform_fraction > 0.50
    assert result.structural_edge_energy > 0.20
    assert result.cloth_score > 0.60
    assert result.visibility < 0.45
    assert not result.passed


def test_metrics_are_deterministic() -> None:
    image = _smooth_skin()
    mask = _ellipse_mask(240, 320)
    first = validate_anatomy(image, mask).to_dict()
    second = validate_anatomy(image, mask).to_dict()
    assert first == second


def test_metrics_are_serializable_and_bounded() -> None:
    result = validate_anatomy(_smooth_skin(), _ellipse_mask(240, 320))
    payload = result.to_dict()
    json.dumps(payload)
    for key in ("texture_score", "cloth_score", "uncovered_score", "visibility"):
        assert 0.0 <= payload[key] <= 1.0


def test_packaged_chef_reference_is_numerically_uncovered() -> None:
    root = Path(__file__).resolve().parents[1]
    example = root / "scripts" / "anatomy_still" / "examples"
    image = np.asarray(Image.open(example / "chef3_final.png").convert("RGB"))
    mask = np.asarray(Image.open(example / "chef3_final_mask.png").convert("L"))
    result = validate_anatomy(image, mask)
    assert result.passed
    assert result.visibility == pytest.approx(0.499409, abs=0.002)
    assert result.cloth_score < 0.50


def test_recipe_library_has_three_complete_scenes_without_h3_anatomy_description() -> None:
    recipes = load_recipes()
    assert {"chef", "katya", "lesbos"} <= set(recipes)
    for scene in ("chef", "katya", "lesbos"):
        recipe = get_recipe(scene)
        assert recipe["source"].endswith(".jpg")
        assert 0.0 < recipe["edit"]["denoise"] < 0.55
        assert recipe["edit"]["steps"] >= 20
        assert recipe["edit"]["seed"] > 0
        assert recipe["edit"]["loras"]
        assert recipe["refine"]["passes"]
        assert recipe["refine"]["passes"][0]["backend"] == "qwen21_masked_refine"
        assert recipe["mask"]["center_shift"] > 0
        suffix = recipe["h3"]["prompt_suffix"].lower()
        assert suffix.strip()
        assert "vulva" not in suffix
        assert "labia" not in suffix
        assert recipe["status"] == "dry_run_unverified"
