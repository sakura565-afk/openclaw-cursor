"""Построение bbox-маски и feather без сети."""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pytest
from PIL import Image, ImageDraw

from scripts.furniture_local.mask import MaskBuildResult, build_mask


def _solid_rgb(path: Path, size: tuple[int, int], color: tuple[int, int, int]) -> None:
    Image.new("RGB", size, color).save(path)


def test_bbox_mask_size_feather_and_alpha(tmp_path: Path) -> None:
    size = (64, 48)
    src = tmp_path / "src.png"
    _solid_rgb(src, size, (100, 120, 80))
    bbox = (16, 12, 48, 36)
    result: MaskBuildResult = build_mask(
        src,
        tmp_path / "out",
        bbox=bbox,
        feather=4.0,
        task_id="t1",
    )
    assert result.source == "bbox"
    assert result.path.is_file()
    with Image.open(result.path) as mask:
        assert mask.size == size
        assert mask.mode == "L"
        arr = np.array(mask)
    assert arr.max() == 255
    # Центр маски должен быть непрозрачным
    cy, cx = 24, 32
    assert arr[cy, cx] > 200
    # Далеко от bbox — почти ноль (с учётом feather на краях)
    assert arr[0, 0] < 30
    assert result.bbox[0] <= bbox[0]
    assert result.area > 0
    assert result.metadata["feather"] == 4.0


def test_mask_from_file_resizes_to_image(tmp_path: Path) -> None:
    src = tmp_path / "src.png"
    _solid_rgb(src, (80, 60), (50, 50, 50))
    small_mask = tmp_path / "m.png"
    Image.new("L", (40, 30), 0).save(small_mask)
    # белый квадрат 20x20
    im = Image.open(small_mask)
    ImageDraw.Draw(im).rectangle([10, 5, 29, 24], fill=255)
    im.save(small_mask)

    result = build_mask(src, tmp_path / "out", mask_file=small_mask, task_id="f")
    with Image.open(result.path) as mask:
        assert mask.size == (80, 60)
    assert result.source == "file"


def test_color_threshold_mask(tmp_path: Path) -> None:
    src = tmp_path / "src.png"
    canvas = Image.new("RGB", (32, 32), (200, 200, 200))
    px = canvas.load()
    for y in range(8, 24):
        for x in range(8, 24):
            px[x, y] = (40, 90, 40)
    canvas.save(src)
    result = build_mask(
        src,
        tmp_path / "out",
        target_rgb=(40, 90, 40),
        color_tolerance=15,
        color_feather=0,
        task_id="c",
    )
    arr = np.array(Image.open(result.path))
    assert arr[16, 16] == 255
    assert arr[0, 0] == 0
    assert result.source == "color"


def test_build_mask_requires_one_source(tmp_path: Path) -> None:
    src = tmp_path / "src.png"
    _solid_rgb(src, (10, 10), (0, 0, 0))
    with pytest.raises(ValueError, match="Нужен один источник"):
        build_mask(src, tmp_path / "out", task_id="x")