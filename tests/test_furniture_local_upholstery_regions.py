"""Тесты примитивов маски обивки (scripts/furniture_local/upholstery_regions.py)."""
import numpy as np
import pytest
from PIL import Image

from scripts.furniture_local.upholstery_regions import (
    band_between,
    cut_below,
    drop_small_components,
    features,
    finalize,
    poly_mask,
    profile_map,
    rect,
    safe_fill,
    verify_outside,
)


def test_poly_and_rect():
    shape = (100, 200)
    p = poly_mask([[(10, 10), (50, 10), (50, 40)]], shape)
    assert p.dtype == bool and p.shape == shape
    assert p[20, 30] and not p[60, 30]
    r = rect(shape, 0, 0, 10, 10)
    assert r[5, 5] and not r[15, 5]


def test_features_separate_warm_fabric_from_cool_wall():
    # тёплая обивка: (140, 110, 90) -> warm = +50
    # холодная стена: (200, 200, 195) -> warm = +5
    img = np.zeros((2, 2, 3), np.uint8)
    img[0, 0] = (140, 110, 90)
    img[0, 1] = (200, 200, 195)
    lum, sat, warm, tex = features(img)
    assert warm[0, 0] > warm[0, 1] + 20
    assert sat[0, 0] > sat[0, 1]
    assert tex.shape == (2, 2)


def test_profile_map_interpolates_and_marks_gaps():
    prof = [(0, 100), (100, 200)]
    y = profile_map(prof, 200)
    assert y[0] == 100 and y[99] == pytest.approx(199)
    assert np.isnan(y[150])


def test_band_between_profiles():
    b = band_between([(0, 10), (50, 10)], [(0, 30), (50, 30)], 100, 60)
    assert b[20, 25] and not b[5, 25] and not b[40, 25]


def test_cut_below_marks_everything_under_profile():
    cut = cut_below([(0, 40), (100, 40)], 100, 80)
    assert cut[50, 10] and not cut[30, 10]


def test_safe_fill_does_not_fill_holes_touching_forbidden():
    s = np.zeros((40, 40), bool)
    s[5:35, 5:35] = True
    s[15:25, 15:25] = False          # дыра внутри
    forbidden = np.zeros((40, 40), bool)
    forbidden[18:22, 18:22] = True   # внутри дыры — «подушка»
    out = safe_fill(s, forbidden)
    assert out[20, 20] is np.False_ or not out[20, 20]
    # чистая дыра без запрета — заливается
    s2 = np.zeros((40, 40), bool)
    s2[5:35, 5:35] = True
    s2[15:25, 15:25] = False
    out2 = safe_fill(s2, np.zeros((40, 40), bool))
    assert out2[20, 20]


def test_drop_small_components():
    s = np.zeros((100, 100), bool)
    s[0:50, 0:50] = True      # 2500 px — выше порога
    s[90:95, 90:95] = True    # 25 px — ниже порога
    out = drop_small_components(s, 2000)
    assert out[10, 10] and not out[92, 92]


def test_finalize_keeps_forbidden_clean():
    s = np.zeros((80, 80), bool)
    s[10:70, 10:70] = True
    forbidden = np.zeros((80, 80), bool)
    forbidden[40:50, 40:50] = True
    out = finalize(s, forbidden, min_px=100)
    assert not out[45, 45]
    assert out[20, 20]


def test_verify_outside_detects_leak():
    src = np.zeros((20, 20, 3), np.uint8)
    res = src.copy()
    res[15, 15] = 255          # изменение ВНЕ маски
    mask = np.zeros((20, 20), bool)
    mask[0:10, 0:10] = True
    assert verify_outside(src, res, mask) == 1
    res2 = src.copy()
    res2[5, 5] = 255           # изменение ВНУТРИ маски — не считается
    assert verify_outside(src, res2, mask) == 0
