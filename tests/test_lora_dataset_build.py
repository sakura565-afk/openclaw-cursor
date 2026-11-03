# -*- coding: utf-8 -*-
"""Tests for the LoRA dataset builder."""
from __future__ import annotations

import importlib.util
import os
from pathlib import Path

import pytest

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
_spec = importlib.util.spec_from_file_location(
    "lora_dataset_build", SCRIPTS / "lora_dataset_build.py")
ds = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(ds)


def _make_image(path, color=(120, 90, 70), size=(64, 96)):
    from PIL import Image
    Image.new("RGB", size, color).save(path)


def _subprocess_env() -> dict:
    """Env for a child that must not depend on the console codec.

    The Windows CI console is cp1251 and the script prints Cyrillic, so a
    child with the default stdout encoding dies with UnicodeEncodeError.
    """
    env = dict(os.environ)
    env["PYTHONIOENCODING"] = "utf-8"
    env["PYTHONUTF8"] = "1"
    return env


def test_list_images_finds_only_images(tmp_path):
    _make_image(tmp_path / "a.jpg")
    _make_image(tmp_path / "b.png")
    (tmp_path / "notes.txt").write_text("x")
    found = ds.list_images(str(tmp_path))
    assert len(found) == 2
    assert all(f.lower().endswith((".jpg", ".png")) for f in found)


def test_list_images_on_empty_dir(tmp_path):
    assert ds.list_images(str(tmp_path)) == []


def test_caption_puts_trigger_first():
    cap = ds.caption_for("/x/Polina_2024_01.jpg", "polina_person")
    assert cap.startswith("polina_person")
    assert "natural skin texture" in cap


def test_caption_keeps_trigger_out_of_the_description():
    """The trigger is what encodes the constant features; it must not be
    duplicated inside the variable description."""
    cap = ds.caption_for("/x/portrait_2023.jpg", "polina_person")
    assert cap.count("polina_person") == 1


def test_caption_strips_year_like_tokens():
    cap = ds.caption_for("/x/polina_2024_01.jpg", "t")
    assert "2024" not in cap


def test_perceptual_key_is_stable_for_copies(tmp_path):
    from PIL import Image
    import numpy as np
    a = np.full((32, 32, 3), 100, dtype="uint8")
    p1 = tmp_path / "a.png"
    p2 = tmp_path / "a_copy.png"
    Image.fromarray(a).save(p1)
    Image.fromarray(a).save(p2)
    assert ds.perceptual_key(str(p1)) == ds.perceptual_key(str(p2))


def test_perceptual_key_separates_different_content(tmp_path):
    from PIL import Image
    import numpy as np
    a = np.full((32, 32, 3), 100, dtype="uint8")
    b = np.full((32, 32, 3), 100, dtype="uint8")
    b[0, 0] = 200
    p1 = tmp_path / "a.png"
    p2 = tmp_path / "b.png"
    Image.fromarray(a).save(p1)
    Image.fromarray(b).save(p2)
    assert ds.perceptual_key(str(p1)) != ds.perceptual_key(str(p2))


def test_perceptual_key_separates_different_colour(tmp_path):
    """Two flat frames of different colour must not collide as duplicates."""
    from PIL import Image
    import numpy as np
    red = np.full((32, 32, 3), (200, 30, 30), dtype="uint8")
    blue = np.full((32, 32, 3), (30, 30, 200), dtype="uint8")
    p1 = tmp_path / "red.png"
    p2 = tmp_path / "blue.png"
    Image.fromarray(red).save(p1)
    Image.fromarray(blue).save(p2)
    assert ds.perceptual_key(str(p1)) != ds.perceptual_key(str(p2))


def test_sharpness_of_flat_image_is_low(tmp_path):
    from PIL import Image
    import numpy as np
    p = tmp_path / "flat.png"
    Image.fromarray(np.full((64, 64, 3), 90, dtype="uint8")).save(p)
    assert ds.sharpness(str(p)) < 1.0


def test_sharpness_of_noisy_image_is_high(tmp_path):
    from PIL import Image
    import numpy as np
    rng = np.random.default_rng(3)
    p = tmp_path / "noise.png"
    Image.fromarray(rng.integers(0, 255, (64, 64, 3), dtype="uint8")).save(p)
    assert ds.sharpness(str(p)) > 10.0


def test_audit_writes_nothing(tmp_path):
    src = tmp_path / "src"
    src.mkdir()
    _make_image(src / "a.jpg")
    out = tmp_path / "out"
    import subprocess
    import sys
    r = subprocess.run(
        [sys.executable, str(SCRIPTS / "lora_dataset_build.py"),
         "--source", str(src), "--audit"],
        capture_output=True, text=True, encoding="utf-8", errors="replace",
        env=_subprocess_env())
    assert r.returncode == 0, r.stderr
    assert not out.exists()


def test_build_writes_image_and_caption(tmp_path):
    src = tmp_path / "src"
    src.mkdir()
    for i in range(5):
        _make_image(src / f"shot{i}.jpg")
    out = tmp_path / "out"
    import subprocess
    import sys
    r = subprocess.run(
        [sys.executable, str(SCRIPTS / "lora_dataset_build.py"),
         "--source", str(src), "--out", str(out), "--trigger", "tperson",
         "--validate", "1"],
        capture_output=True, text=True, encoding="utf-8", errors="replace",
        env=_subprocess_env())
    assert r.returncode == 0, r.stderr
    assert (out / "dataset_summary.json").exists()
    caps = list(out.glob("*.txt"))
    assert len(caps) >= 1
    assert caps[0].read_text(encoding="utf-8").startswith("tperson")
    val = out / "validation"
    assert val.is_dir() and len(list(val.iterdir())) == 1


def test_build_without_out_is_rejected(tmp_path):
    src = tmp_path / "src"
    src.mkdir()
    _make_image(src / "a.jpg")
    import subprocess
    import sys
    r = subprocess.run(
        [sys.executable, str(SCRIPTS / "lora_dataset_build.py"),
         "--source", str(src)],
        capture_output=True, text=True, encoding="utf-8", errors="replace",
        env=_subprocess_env())
    assert r.returncode != 0
