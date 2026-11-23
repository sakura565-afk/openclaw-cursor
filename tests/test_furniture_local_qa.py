"""Синтетическая приёмка PASS/FAIL и коллаж."""

from __future__ import annotations

from pathlib import Path

import numpy as np
from PIL import Image

from scripts.furniture_local.qa import QAThresholds, build_collage, evaluate_acceptance


def _write_rgb(path: Path, arr: np.ndarray) -> None:
    Image.fromarray(arr.astype(np.uint8), mode="RGB").save(path)


def _write_mask(path: Path, inside: np.ndarray) -> None:
    m = np.where(inside, 255, 0).astype(np.uint8)
    Image.fromarray(m, mode="L").save(path)


def test_good_result_passes_and_writes_collage(tmp_path: Path) -> None:
    h, w = 64, 64
    ref = np.full((h, w, 3), 120, dtype=np.uint8)
    res = ref.copy()
    inside = np.zeros((h, w), dtype=bool)
    inside[20:44, 20:44] = True
    res[inside] = [200, 50, 50]
    ref_p, res_p, mask_p = tmp_path / "ref.png", tmp_path / "res.png", tmp_path / "mask.png"
    coll = tmp_path / "collage.png"
    _write_rgb(ref_p, ref)
    _write_rgb(res_p, res)
    _write_mask(mask_p, inside)

    report = evaluate_acceptance(
        ref_p,
        res_p,
        mask_p,
        coll,
        thresholds=QAThresholds(max_inside_lab_shift=999.0),
    )
    assert report.passed
    assert report.verdict == "PASS"
    assert coll.is_file()
    with Image.open(coll) as im:
        assert im.width > w


def test_bad_outside_change_fails(tmp_path: Path) -> None:
    h, w = 48, 48
    ref = np.full((h, w, 3), 100, dtype=np.uint8)
    res = ref.copy()
    inside = np.zeros((h, w), dtype=bool)
    inside[16:32, 16:32] = True
    res[inside] = [180, 180, 40]
    res[0:8, :] = [10, 10, 250]
    ref_p, res_p, mask_p = tmp_path / "r.png", tmp_path / "o.png", tmp_path / "m.png"
    _write_rgb(ref_p, ref)
    _write_rgb(res_p, res)
    _write_mask(mask_p, inside)
    coll = tmp_path / "c.png"

    report = evaluate_acceptance(ref_p, res_p, mask_p, coll)
    assert not report.passed
    assert report.verdict == "FAIL"
    assert any("силуэта" in r.lower() or "вне маски" in r.lower() for r in report.reasons)
    assert coll.is_file()


def test_build_collage_labels(tmp_path: Path) -> None:
    ref = tmp_path / "a.png"
    res = tmp_path / "b.png"
    Image.new("RGB", (20, 10), (255, 0, 0)).save(ref)
    Image.new("RGB", (20, 10), (0, 255, 0)).save(res)
    out = tmp_path / "side.png"
    build_collage(ref, res, out, verdict="PASS")
    assert out.stat().st_size > 100