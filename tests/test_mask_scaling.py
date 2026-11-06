"""The pose mask and the frame it is compared against have different sizes.

The mask comes from the source photo (853x1280) and the frames come from the
render (736x1120). The validator rejects a mismatch with "mask and image
dimensions differ", so the mask has to be scaled. Rebuilding it on the frame
would place it somewhere else, because the picked frame is a macro close-up
and the pose detector sees a different pose there.

The scaling lives in one helper so that it cannot drift between the three
call sites: the picker, the H3 frame picker and the pipeline.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import numpy as np  # noqa: E402

from scripts.anatomy_still.pick_still import fit_mask_to_image  # noqa: E402


def test_a_mask_of_another_size_is_scaled_to_the_image() -> None:
    mask = np.zeros((1280, 853), dtype=np.uint8)
    mask[400:700, 200:500] = 255
    image = np.zeros((1120, 736, 3), dtype=np.uint8)
    scaled = fit_mask_to_image(mask, image)
    assert scaled.shape[:2] == image.shape[:2]
    assert scaled.shape == (1120, 736)


def test_a_mask_of_the_same_size_is_returned_unchanged() -> None:
    mask = np.zeros((1120, 736), dtype=np.uint8)
    mask[100:200, 100:200] = 255
    image = np.zeros((1120, 736, 3), dtype=np.uint8)
    scaled = fit_mask_to_image(mask, image)
    assert scaled is mask


def test_the_scaling_keeps_the_mask_over_the_same_place() -> None:
    """The mask must stay where it was, only resized."""
    mask = np.zeros((1280, 853), dtype=np.uint8)
    mask[600:700, 300:400] = 255
    image = np.zeros((1120, 736, 3), dtype=np.uint8)
    scaled = fit_mask_to_image(mask, image)
    ys, xs = np.nonzero(scaled)
    # the same region, expressed in the smaller frame
    assert abs(ys.min() - 600 * 736 / 853) < 12
    assert abs(xs.min() - 300 * 1120 / 1280) < 12


def test_all_three_call_sites_use_the_shared_helper() -> None:
    repo = Path(__file__).resolve().parents[1]
    helper = repo / "scripts/anatomy_still/pick_still.py"
    for rel in ("scripts/anatomy_still/pick_still.py",
                "scripts/anatomy_still/h3_still.py",
                "scripts/anatomy_still/pipeline.py"):
        text = (repo / rel).read_text(encoding="utf-8")
        assert "fit_mask_to_image" in text, rel
        # The inline resize is what drifted between the three places. It may
        # only live in the helper itself, once.
        count = text.count("cv2.resize(mask")
        expected = 1 if rel == "scripts/anatomy_still/pick_still.py" else 0
        assert count == expected, (rel, count)
