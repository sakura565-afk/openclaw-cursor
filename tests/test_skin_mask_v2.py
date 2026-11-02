"""Tests for person-aware skin masking on hostile warm backgrounds."""

from __future__ import annotations

from pathlib import Path
import subprocess
import sys

import numpy as np
import pytest
from PIL import Image, ImageDraw

import scripts.skin_mask_v2 as skin_module
from scripts.skin_mask_v2 import skin_area_fraction, skin_mask_v2


def blob_image(background: tuple[int, int, int]) -> np.ndarray:
    image = Image.new("RGB", (120, 100), background)
    ImageDraw.Draw(image).ellipse((35, 20, 84, 79), fill=(205, 145, 112))
    return np.asarray(image)


def test_skin_on_dark_background_is_selected() -> None:
    mask = skin_mask_v2(blob_image((30, 30, 40)), use_comfy=False, feather=0)

    assert mask.shape == (100, 120)
    assert mask.dtype == np.float32
    assert mask[50, 60] > 0.95
    assert mask[5, 5] == 0.0
    assert 0.05 < float(np.mean(mask > 0.5)) < 0.20


def test_beige_stone_wall_is_rejected_but_skin_blob_is_kept() -> None:
    image = blob_image((190, 170, 145))

    mask = skin_mask_v2(image, use_comfy=False, feather=0)

    assert mask[50, 60] > 0.95
    assert float(np.mean(mask[:15, :] > 0.5)) < 0.01
    assert float(np.mean(mask > 0.5)) < 0.20


def test_person_mask_limits_skin_to_the_selected_person() -> None:
    image = Image.new("RGB", (160, 100), (35, 40, 45))
    draw = ImageDraw.Draw(image)
    draw.ellipse((15, 20, 64, 79), fill=(205, 145, 112))
    draw.ellipse((95, 20, 144, 79), fill=(205, 145, 112))
    person = np.zeros((100, 160), dtype=np.uint8)
    person[10:90, 5:75] = 255

    mask = skin_mask_v2(np.asarray(image), person_mask=person, use_comfy=False, feather=0)

    assert mask[50, 40] > 0.95
    assert np.count_nonzero(mask[:, 80:]) == 0


def test_tiny_disconnected_skin_components_are_removed() -> None:
    image_array = blob_image((30, 30, 40)).copy()
    image_array[50, 105] = (205, 145, 112)

    mask = skin_mask_v2(image_array, use_comfy=False, feather=0, min_component_fraction=0.001)

    assert mask[50, 60] > 0.95
    assert mask[50, 105] == 0.0


def test_feather_produces_soft_edges_and_preserves_interior() -> None:
    mask = skin_mask_v2(blob_image((30, 30, 40)), use_comfy=False, feather=3)

    assert mask[50, 60] > 0.99
    assert np.any((mask > 0.05) & (mask < 0.95))
    assert float(mask.min()) >= 0.0
    assert float(mask.max()) <= 1.0


def test_skin_area_fraction_counts_confident_mask_pixels() -> None:
    mask = np.zeros((10, 20), dtype=np.float32)
    mask[:5, :10] = 1.0
    mask[5:, :10] = 0.25

    assert skin_area_fraction(mask) == 0.25


def test_oversized_mask_warns_about_possible_background_capture() -> None:
    image = Image.new("RGB", (100, 100), (25, 30, 35))
    ImageDraw.Draw(image).rectangle((10, 10, 89, 89), fill=(205, 145, 112))

    with pytest.warns(UserWarning, match="35%"):
        mask = skin_mask_v2(np.asarray(image), use_comfy=False, feather=0)

    assert skin_area_fraction(mask) > 0.35


def test_comfyui_failure_degrades_to_chromatic_fallback(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    image = blob_image((30, 30, 40))

    def unavailable(*args: object, **kwargs: object) -> np.ndarray:
        raise OSError("ComfyUI is offline")

    monkeypatch.setattr(skin_module, "comfy_person_mask", unavailable)
    with pytest.warns(UserWarning, match="chromatic fallback"):
        fallback = skin_mask_v2(image, use_comfy=True, feather=0)
    expected = skin_mask_v2(image, use_comfy=False, feather=0)

    np.testing.assert_array_equal(fallback, expected)


def test_cli_writes_mask_and_visual_overlay(tmp_path: Path) -> None:
    source = tmp_path / "source.png"
    output = tmp_path / "mask.png"
    overlay = tmp_path / "overlay.png"
    Image.fromarray(blob_image((190, 170, 145))).save(source)
    script = Path(__file__).resolve().parents[1] / "scripts" / "skin_mask_v2.py"

    subprocess.run(
        [
            sys.executable,
            str(script),
            str(source),
            "--out",
            str(output),
            "--viz",
            str(overlay),
            "--no-comfy",
            "--feather",
            "0",
        ],
        check=True,
        capture_output=True,
        text=True,
    )

    saved_mask = np.asarray(Image.open(output).convert("L"))
    assert saved_mask[50, 60] > 250
    assert float(np.mean(saved_mask[:15] > 128)) < 0.01
    assert Image.open(overlay).size == (120, 100)
