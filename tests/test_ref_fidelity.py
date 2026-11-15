"""Contracts for reference-vs-render fidelity scoring (Pillow only)."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest
from PIL import Image, ImageDraw

from tools.ref_fidelity import RefFidelity


def _chair_like(draw: ImageDraw.ImageDraw, size: int) -> None:
    """Simple silhouette: back + seat rectangle (stable across backgrounds)."""
    m = size // 8
    draw.rectangle((m, m, size - m, size // 2), fill=(30, 30, 30))
    draw.rectangle((m, size // 2, size - m, size - m), fill=(45, 45, 45))


def _blob(draw: ImageDraw.ImageDraw, size: int) -> None:
    pad = size // 5
    draw.ellipse((pad, pad, size - pad, size - pad), fill=(20, 80, 140))


def _block(draw: ImageDraw.ImageDraw, size: int) -> None:
    pad = size // 4
    draw.rectangle((pad, pad, size - pad, size - pad), fill=(180, 40, 40))


def _synthetic(draw_fn, bg: tuple[int, int, int], size: int = 160) -> Image.Image:
    img = Image.new("RGB", (size, size), bg)
    draw = ImageDraw.Draw(img)
    draw_fn(draw, size)
    return img


def test_identical_image_scores_same(tmp_path: Path) -> None:
    path = tmp_path / "same.png"
    img = _synthetic(_chair_like, (250, 250, 250))
    img.save(path)
    out = tmp_path / "out"
    result = RefFidelity().compare(str(path), str(path), str(out))
    assert result["edge_iou"] > 0.9
    assert result["verdict"] == "same"


def test_same_subject_different_background_not_replaced(tmp_path: Path) -> None:
    ref = tmp_path / "ref.png"
    rend = tmp_path / "rend.png"
    _synthetic(_chair_like, (255, 255, 255)).save(ref)
    _synthetic(_chair_like, (20, 40, 180)).save(rend)
    result = RefFidelity().compare(str(ref), str(rend), str(tmp_path / "out"))
    assert result["verdict"] in {"same", "drifted"}


def test_different_subject_verdict_replaced(tmp_path: Path) -> None:
    ref = tmp_path / "ref.png"
    rend = tmp_path / "rend.png"
    _synthetic(_blob, (255, 255, 255)).save(ref)
    _synthetic(_block, (255, 255, 255)).save(rend)
    result = RefFidelity().compare(str(ref), str(rend), str(tmp_path / "out"))
    assert result["verdict"] == "replaced"


def test_compare_is_deterministic(tmp_path: Path) -> None:
    ref = tmp_path / "ref.png"
    rend = tmp_path / "rend.png"
    _synthetic(_chair_like, (240, 240, 240)).save(ref)
    _synthetic(_chair_like, (10, 10, 10)).save(rend)
    out_a = tmp_path / "a"
    out_b = tmp_path / "b"
    first = RefFidelity().compare(str(ref), str(rend), str(out_a))
    second = RefFidelity().compare(str(ref), str(rend), str(out_b))
    keys = ("edge_iou", "edge_dice", "palette_distance", "bbox_ratio_match", "verdict")
    assert {k: first[k] for k in keys} == {k: second[k] for k in keys}


def test_missing_file_raises_clear_error(tmp_path: Path) -> None:
    existing = tmp_path / "ref.png"
    _synthetic(_chair_like, (255, 255, 255)).save(existing)
    checker = RefFidelity()
    with pytest.raises(FileNotFoundError, match="Render image not found"):
        checker.compare(str(existing), str(tmp_path / "missing.png"), str(tmp_path / "out"))
    with pytest.raises(FileNotFoundError, match="Reference image not found"):
        checker.compare(str(tmp_path / "nope.png"), str(existing), str(tmp_path / "out"))


def test_cli_missing_file_exits_with_message(tmp_path: Path, capsys: pytest.CaptureFixture) -> None:
    script = Path(__file__).resolve().parents[1] / "tools" / "ref_fidelity.py"
    proc = subprocess.run(
        [
            sys.executable,
            str(script),
            "--reference",
            str(tmp_path / "missing_ref.png"),
            "--render",
            str(tmp_path / "missing_render.png"),
            "--out",
            str(tmp_path / "cli_out"),
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    assert proc.returncode == 2
    assert "not found" in proc.stderr.lower()


def test_cli_writes_json_metrics(tmp_path: Path) -> None:
    ref = tmp_path / "ref.png"
    rend = tmp_path / "rend.png"
    _synthetic(_chair_like, (255, 255, 255)).save(ref)
    _synthetic(_chair_like, (255, 255, 255)).save(rend)
    script = Path(__file__).resolve().parents[1] / "tools" / "ref_fidelity.py"
    out_dir = tmp_path / "cli"
    proc = subprocess.run(
        [
            sys.executable,
            str(script),
            "--reference",
            str(ref),
            "--render",
            str(rend),
            "--out",
            str(out_dir),
        ],
        capture_output=True,
        text=True,
        check=True,
    )
    payload = json.loads(proc.stdout)
    assert payload["verdict"] == "same"
    assert Path(payload["collage"]).is_file()