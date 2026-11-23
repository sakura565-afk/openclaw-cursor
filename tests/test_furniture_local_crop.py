"""Тесты модулей crop_mode / upholstery_mask / paste_back.

Закрывают реальные дефекты, найденные на фото:
  - _square_crop_box отдавал бокс 776x776 при target 768 -> restore_crop
    рассинхронизировался с кропом;
  - fill-модели нужна СПЛОШНАЯ маска, дробная не перекрашивает;
  - paste-back должен не трогать ничего вне точной маски ткани.
"""

from __future__ import annotations

from pathlib import Path

import numpy as np
from PIL import Image

from scripts.furniture_local.crop_mode import compute_tiles, prepare_crop, restore_crop
from scripts.furniture_local.paste_back import paste_back
from scripts.furniture_local.upholstery_mask import build_upholstery_mask


def _solid_image(path: Path, size: tuple[int, int] = (200, 160), color=(180, 165, 145)) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    Image.new("RGB", size, color).save(path)
    return path


def test_square_crop_box_side_equals_target(tmp_path: Path) -> None:
    """Сторона кропа обязана быть ровно target, иначе restore_crop расходится."""
    src = _solid_image(tmp_path / "src.png", (1264, 848))
    mask = Image.new("L", (1264, 848), 0)
    from PIL import ImageDraw as _ID

    _ID.Draw(mask).rectangle([150, 445, 1105, 618], fill=255)
    mask.save(tmp_path / "mask.png")

    info = prepare_crop(src, tmp_path / "mask.png", tmp_path / "out", target=768, bbox=(150, 445, 1105, 618))
    x0, y0, x1, y1 = info["box"]
    assert x1 - x0 == 768, info["box"]
    assert y1 - y0 == 768, info["box"]
    assert info["size"] == [768, 768]


def test_compute_tiles_covers_wide_region(tmp_path: Path) -> None:
    """Область шире 768 px должна резаться на несколько тайлов с перекрытием."""
    tiles = compute_tiles((150, 445, 1105, 618), (1264, 848), target=768, overlap=96)
    assert len(tiles) >= 2, tiles
    for x0, y0, x1, y1 in tiles:
        assert x1 - x0 == 768 and y1 - y0 == 768, (x0, y0, x1, y1)
    # перекрытие по горизонтали
    assert tiles[0][2] > tiles[1][0]
    # всё в пределах кадра
    for x0, y0, x1, y1 in tiles:
        assert 0 <= x0 and x1 <= 1264
        assert 0 <= y0 and y1 <= 848


def test_compute_tiles_single_for_narrow_region() -> None:
    tiles = compute_tiles((495, 175, 930, 590), (1408, 768), target=768, overlap=96)
    assert len(tiles) == 1, tiles


def test_paste_back_does_not_touch_outside_mask(tmp_path: Path) -> None:
    """Вне маски не должно измениться ни одного пикселя."""
    ref = _solid_image(tmp_path / "ref.png", (120, 90), (180, 165, 145))
    gen = _solid_image(tmp_path / "gen.png", (120, 90), (20, 40, 120))
    mask = Image.new("L", (120, 90), 0)
    from PIL import ImageDraw as _ID

    _ID.Draw(mask).rectangle([30, 20, 90, 70], fill=255)
    mask.save(tmp_path / "mask.png")

    out = tmp_path / "pb.png"
    metrics = paste_back(ref, gen, tmp_path / "mask.png", out, feather=6.0, erode_px=2)

    a = np.asarray(Image.open(ref).convert("RGB")).astype(int)
    b = np.asarray(Image.open(out).convert("RGB")).astype(int)
    m = np.asarray(Image.open(tmp_path / "mask.png").convert("L")) > 127
    changed = np.abs(a - b).sum(axis=2) > 25
    assert changed[~m].sum() == 0, "пиксели вне маски изменились"
    assert changed[m].mean() > 0.5, "внутри маски ничего не изменилось"
    assert metrics["pasteback_outside_change_fraction"] == 0.0


def test_paste_back_keeps_fabric_colour_change(tmp_path: Path) -> None:
    """Внутри маски должен появиться новый цвет (синий вместо бежевого)."""
    ref = _solid_image(tmp_path / "ref.png", (120, 90), (180, 165, 145))
    gen = _solid_image(tmp_path / "gen.png", (120, 90), (20, 40, 120))
    mask = Image.new("L", (120, 90), 0)
    from PIL import ImageDraw as _ID

    _ID.Draw(mask).rectangle([30, 20, 90, 70], fill=255)
    mask.save(tmp_path / "mask.png")

    out = tmp_path / "pb.png"
    paste_back(ref, gen, tmp_path / "mask.png", out, feather=6.0, erode_px=2)
    a = np.asarray(Image.open(ref).convert("RGB")).astype(float)
    b = np.asarray(Image.open(out).convert("RGB")).astype(float)
    m = np.asarray(Image.open(tmp_path / "mask.png").convert("L")) > 127
    br_ref = a[m][:, 2].mean() - a[m][:, 0].mean()
    br_out = b[m][:, 2].mean() - b[m][:, 0].mean()
    assert br_out > br_ref + 30, (br_ref, br_out)


def test_upholstery_mask_selects_fabric_not_pillows(tmp_path: Path) -> None:
    """Маска по цвету должна брать бежевую ткань и не брать пурпурные подушки."""
    img = Image.new("RGB", (200, 120), (185, 170, 148))  # бежевая ткань
    from PIL import ImageDraw as _ID

    d = _ID.Draw(img)
    d.rectangle([20, 20, 90, 60], fill=(120, 70, 110))  # пурпурная подушка
    d.rectangle([0, 95, 200, 120], fill=(60, 40, 30))  # тёмные ножки
    src = tmp_path / "sofa.png"
    img.save(src)

    info = build_upholstery_mask(src, (0, 0, 200, 90), tmp_path / "m.png", feather=0)
    m = np.asarray(Image.open(tmp_path / "m.png").convert("L")) > 127
    a = np.asarray(img).astype(int)

    purple = (a[:, :, 0] > 90) & (a[:, :, 2] > 90) & (a[:, :, 1] < a[:, :, 0] - 15)
    assert (m & purple).sum() == 0, "пурпурная подушка попала в маску"
    assert m.sum() > 100, "ткань не выделена"
    assert info["bbox"][1] >= 0


def test_restore_crop_keeps_frame_outside(tmp_path: Path) -> None:
    """Возврат кропа в полный кадр не должен менять область вне кропа."""
    full = np.zeros((400, 400, 3), dtype=np.uint8)
    full[:, :] = (200, 200, 200)
    full[100:200, 100:200] = (10, 20, 200)  # «сгенерированная» область внутри кропа
    Image.fromarray(full).save(tmp_path / "full.png")
    crop = Image.fromarray(full[100:200, 100:200])
    crop.save(tmp_path / "crop.png")

    out = tmp_path / "restored.png"
    restore_crop(tmp_path / "crop.png", tmp_path / "full.png", [100, 100, 200, 200], out)
    res = np.asarray(Image.open(out).convert("RGB")).astype(int)
    orig = np.asarray(Image.open(tmp_path / "full.png").convert("RGB")).astype(int)
    outside = np.ones((400, 400), bool)
    outside[100:200, 100:200] = False
    assert np.array_equal(res[outside], orig[outside]), "вне кропа изменилось"
    assert res[150, 150].tolist() == [10, 20, 200]
