"""Тесты детерминированной перекраски дивана (sofa_mask / sofa_render).

Модули появились после разбора претензии «старая ткань около пледа и
около подушки». Ключевые инварианты, которые нельзя сломать:

* бордо не попадает на защищённые объекты (плед, подушки, ваза, букет);
* маска и protect не пересекаются;
* гладкая обивка не попадает в protect (иначе она останется серой);
* перекраска не меняет геометрию (светлота/швы сохраняются);
* финальный цвет винно-бордовый и близок к эталону.

Геометрия в sofa_mask привязана к реальному кадру 1280x960, поэтому
тесты строят СИНТЕТИЧЕСКУЮ сцену того же размера: обивка с нужными
числовыми признаками (warm/sat/текстура) + зона вязки + подушка.
Проверяются управления, а не координаты чужого фото.
"""

from __future__ import annotations

import numpy as np
import pytest
from PIL import Image, ImageDraw
from scipy import ndimage

from scripts.furniture_local.sofa_mask import (
    PILLOW_GREEN,
    PILLOW_LIGHT,
    _poly,
    build_masks,
    feather_alpha,
    set_source_reference,
)
from scripts.furniture_local.sofa_render import recolor_sofa

W, H = 1280, 960


def _upholstery_rgb(x: int, y: int) -> tuple[int, int, int]:
    """Тёплая гладкая обивка: warm~26, sat~0.25, низкая текстура."""
    v = 148 + int(10 * np.sin(x / 90.0) * np.cos(y / 70.0))
    return (v, int(v * 0.855), int(v * 0.695))


def _scene() -> Image.Image:
    img = Image.new("RGB", (W, H), (150, 132, 108))
    px = img.load()
    for y in range(H):
        for x in range(W):
            px[x, y] = _upholstery_rgb(x, y)
    d = ImageDraw.Draw(img)
    # Вязаный плед: плотная сетка нитей -> высокая локальная текстура.
    for y in range(480, 700):
        for x in range(250, 640):
            if (x % 4 == 0) or (y % 4 == 0):
                d.point((x, y), fill=(104, 100, 92))
    # Светлая подушка: гладкая, низкая сатигурация.
    d.rectangle([938, 438, 1070, 600], fill=(198, 192, 182))
    return img


@pytest.fixture(scope="module")
def scene_path(tmp_path_factory):
    p = tmp_path_factory.mktemp("sofa") / "scene.png"
    _scene().save(p)
    set_source_reference(p)
    return p


@pytest.fixture(scope="module")
def masks(scene_path):
    return build_masks(scene_path)


@pytest.fixture(scope="module")
def etalon(tmp_path_factory):
    d = tmp_path_factory.mktemp("etalon")
    et = d / "etalon.png"
    em = d / "etalon_mask.png"
    Image.new("RGB", (64, 64), (72, 26, 44)).save(et)
    Image.new("L", (64, 64), 255).save(em)
    return et, em


# --------------------------------------------------------------------------
# маска
# --------------------------------------------------------------------------

def test_mask_has_content(masks):
    mask, protect, stats = masks
    assert mask.shape == (H, W)
    assert mask.sum() > 10000
    assert "mask_px" in stats


def test_protect_never_overlaps_mask(masks):
    """Защищённое и закрашиваемое не должны пересекаться."""
    mask, protect, _ = masks
    assert not (mask & protect).any()


def test_pillows_are_protected(masks):
    """Подушки не должны попадать в закрашиваемую область."""
    mask, protect, _ = masks
    for poly_pts in (PILLOW_GREEN, PILLOW_LIGHT):
        poly = _poly((H, W), poly_pts)
        assert not (poly & mask).any()


def test_knit_is_protected(scene_path, masks):
    """Нити пледа должны быть защищены от закрашивания."""
    mask, protect, _ = masks
    lum = np.asarray(Image.open(scene_path).convert("L")).astype(float)
    tex = ndimage.uniform_filter(np.abs(ndimage.laplace(lum)), size=9)
    threads = tex >= 22
    assert threads.any()
    assert (threads & protect).sum() > 0


def test_smooth_upholstery_not_protected(scene_path, masks):
    """Гладкая обивка не должна попадать в protect (иначе останется серой).

    Исключения — сами объекты (подушка гладкая по определению) и зоны
    кадра, вообще не относящиеся к дивану (окно справа, за которым
    ничего не красится). Проверяется protect за вычетом этих зон:
    там гладкой обивки быть не должно. Это ровно тот дефект, который
    закрывался правкой края пледа — дилатация захватывала солнечную
    обивку (tex 6..10) рядом с пледом.
    """
    import scripts.furniture_local.sofa_mask as sm

    mask, protect, _ = masks
    lum = np.asarray(Image.open(scene_path).convert("L")).astype(float)
    tex = ndimage.uniform_filter(np.abs(ndimage.laplace(lum)), size=9)
    smooth = tex < 12
    excluded = (sm._poly((H, W), sm.PILLOW_GREEN)
                | sm._poly((H, W), sm.PILLOW_LIGHT)
                | sm._poly((H, W), sm.PILLOW_OLIVE)
                | sm._poly((H, W), sm.VASE)
                | sm._poly((H, W), sm.BOUQUET)
                | sm._poly((H, W), sm.TRIPOD)
                | sm._rect((H, W), 1118, 330, 1280, 960)
                | sm._rect((H, W), 1060, 440, 1084, 506)
                | sm._rect((H, W), 1036, 590, 1066, 644)
                | sm._rect((H, W), 1036, 440, 1080, 502)
                | sm._poly((H, W), sm.WOOD)
                # тень под пледом: она гладкая (tex 8..11), тёмная
                # (lum 20..27) и лежит ВНУТРИ полигона пледа. Её нельзя
                # красить: светлое бордо в тени выглядит как пятно.
                | sm._poly((H, W), sm.BLANKET_NARROW)
                | sm._poly((H, W), sm.BLANKET_WIDE))
    assert (smooth & protect & ~excluded).sum() == 0


# --------------------------------------------------------------------------
# альфа-композит
# --------------------------------------------------------------------------

def test_alpha_one_inside_zero_outside(scene_path):
    sel = np.zeros((H, W), bool)
    sel[400:700, 700:900] = True
    protect = np.zeros((H, W), bool)

    set_source_reference(scene_path)
    alpha = feather_alpha(sel, protect, feather_px=1.2, grow_px=0)

    assert alpha.shape == sel.shape
    assert alpha[sel].min() == pytest.approx(1.0)
    assert alpha.max() <= 1.0
    assert alpha.min() >= 0.0


def test_alpha_zero_on_protected(scene_path):
    sel = np.zeros((H, W), bool)
    sel[400:700, 700:900] = True
    protect = np.zeros((H, W), bool)
    protect[600:700, 700:900] = True

    set_source_reference(scene_path)
    alpha = feather_alpha(sel, protect, feather_px=1.2, grow_px=0)

    assert alpha[protect].max() == pytest.approx(0.0)


def test_alpha_does_not_grow_into_wall(scene_path):
    """Рост края ограничен тканью: на стену альфа не заходит."""
    sel = np.zeros((H, W), bool)
    sel[400:700, 700:900] = True
    protect = np.zeros((H, W), bool)

    set_source_reference(scene_path)
    alpha = feather_alpha(sel, protect, feather_px=1.2, grow_px=3)

    # за маской альфа должна быть нулевой везде, кроме узкой полосы
    outside = ~sel
    assert (alpha[outside] > 0.5).sum() < 4000


# --------------------------------------------------------------------------
# перекраска
# --------------------------------------------------------------------------

def test_recolor_output_is_burgundy(scene_path, masks, etalon, tmp_path):
    et, em = etalon
    out = tmp_path / "out.png"
    mask, protect, _ = masks
    met = recolor_sofa(scene_path, et, em, out, mask=mask, protect=protect)

    r, g, b = met["result_median_rgb"]
    assert r > b
    assert met["R_minus_B"] > 20
    assert met["result_sat_p50"] > 0.3


def test_recolor_keeps_geometry(scene_path, masks, etalon, tmp_path):
    """Перекраска не должна двигать геометрию: края остаются на месте."""
    et, em = etalon
    out = tmp_path / "out_geo.png"
    mask, protect, _ = masks
    recolor_sofa(scene_path, et, em, out, mask=mask, protect=protect)

    src = np.asarray(Image.open(scene_path).convert("RGB")).astype(float)
    res = np.asarray(Image.open(out).convert("RGB")).astype(float)
    assert res.shape == src.shape

    def edge_energy(a):
        return float(np.abs(np.diff(a.mean(axis=2), axis=1)).mean())

    assert edge_energy(res) == pytest.approx(edge_energy(src), rel=0.35)


def test_recolor_does_not_touch_protected(scene_path, masks, etalon, tmp_path):
    """Ни один защищённый пиксель не должен измениться."""
    et, em = etalon
    out = tmp_path / "out_prot.png"
    mask, protect, _ = masks
    recolor_sofa(scene_path, et, em, out, mask=mask, protect=protect)

    src = np.asarray(Image.open(scene_path).convert("RGB")).astype(float)
    res = np.asarray(Image.open(out).convert("RGB")).astype(float)
    changed = np.abs(src - res).max(axis=2) > 6
    assert (changed & protect).sum() == 0


def test_recolor_changes_upholstery(scene_path, masks, etalon, tmp_path):
    """Обивка должна быть перекрашена, а не остаться исходной."""
    et, em = etalon
    out = tmp_path / "out_change.png"
    mask, protect, _ = masks
    recolor_sofa(scene_path, et, em, out, mask=mask, protect=protect)

    src = np.asarray(Image.open(scene_path).convert("RGB")).astype(float)
    res = np.asarray(Image.open(out).convert("RGB")).astype(float)
    changed = np.abs(src - res).max(axis=2) > 6
    assert (changed & mask).sum() > 10000
