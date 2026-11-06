"""Regression tests for the ACE-Step fetch script.

The script failed twice in the same shape: the download worked, the file was
moved into place, and then the script died on the bookkeeping afterwards.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from scripts.fetch_music_models import (  # noqa: E402
    ACE_FILES,
    _repo_layout_dir,
    plan,
)


def test_repo_layout_dir_matches_where_the_hub_will_put_the_file() -> None:
    """The hub keeps the repository layout under local_dir.

    With ``local_dir`` it does not create the nested directory itself, so the
    copy fails with FileNotFoundError unless the script creates it first.
    """
    local_dir = Path("G:/AI/Models/comfyui/diffusion_models")
    layout = _repo_layout_dir(local_dir, "split_files/diffusion_models/x.safetensors")
    assert layout == local_dir / "split_files" / "diffusion_models"


def test_plan_targets_the_comfyui_model_folders() -> None:
    items = plan("G:/AI/Models/comfyui")
    names = {dst.name for _rel, dst in items}
    assert names == {Path(rel).name for rel, _folder in ACE_FILES}
    for _rel, dst in items:
        assert dst.parent.name in {"diffusion_models", "text_encoders", "vae"}


def test_the_move_bookkeeping_uses_a_path_and_not_a_string() -> None:
    """hf_hub_download returns a str, and str has no .parent.

    That AttributeError ended the first run right after the first file had
    been moved into place, so the remaining files were never fetched.
    """
    src = (Path(__file__).resolve().parents[1] / "scripts" / "fetch_music_models.py")
    text = src.read_text(encoding="utf-8")
    assert "got_path = Path(got)" in text
    assert "got.parent" not in text


def test_an_existing_file_is_not_downloaded_again() -> None:
    """Re-running the script must not burn gigabytes on the same bytes."""
    src = (Path(__file__).resolve().parents[1] / "scripts" / "fetch_music_models.py")
    text = src.read_text(encoding="utf-8")
    assert "уже на месте" in text
    assert "--force" in text
