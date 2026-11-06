"""Tests for the music-video motion design.

The crop maths is the part that can silently ruin a take: a window that is
one scale factor off turns a 1.00 framing into an extreme close-up and cuts
the subject out of the frame.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from scripts.music_video_motion import (  # noqa: E402
    FACE_OUT_X_START,
    FACE_X,
    EYES_OUT_Y_START,
    EYES_Y,
    HEADROOM,
    PRESETS,
    SRC_H,
    SRC_W,
    _crop_x_expr,
    _crop_y_expr,
    _flash_expr,
    _zoom_expr,
    build_filter,
)


def _window(zoom: float, out_x: float, out_y: float) -> tuple[float, float, float, float]:
    """The crop window in the scaled frame for a given zoom and framing."""
    width = SRC_W * HEADROOM / zoom
    height = SRC_H * HEADROOM / zoom
    x = FACE_X * HEADROOM - out_x * HEADROOM / zoom
    y = EYES_Y * HEADROOM - out_y * HEADROOM / zoom
    return x, y, width, height


def test_at_zoom_one_the_window_is_the_whole_frame() -> None:
    """Zoom 1.00 has to reproduce the source framing exactly."""
    x, y, width, height = _window(1.0, FACE_OUT_X_START, EYES_OUT_Y_START)
    assert width == SRC_W * HEADROOM
    assert height == SRC_H * HEADROOM
    assert x == 0
    assert y == 0


def test_the_window_stays_inside_the_scaled_frame() -> None:
    """A window larger than the frame is what produced the broken crop.

    The zoom and the framing drift together along the clip, so the pairs are
    taken from that trajectory - at zoom 1.00 the face has to sit where it
    sits in the source, otherwise the framing does not match the original.
    """
    scaled_w = SRC_W * HEADROOM
    scaled_h = SRC_H * HEADROOM
    duration = 7.29
    for name, look in PRESETS.items():
        zoom_end = float(look["zoom_end"])
        for step in range(0, 21):
            ratio = step / 20.0
            zoom = 1.0 + (zoom_end - 1.0) * ratio
            out_x = FACE_OUT_X_START + float(look["drift_x"]) * ratio
            out_y = EYES_OUT_Y_START + float(look["drift_y"]) * ratio
            x, y, width, height = _window(zoom, out_x, out_y)
            assert x >= -0.5, (name, zoom)
            assert y >= -0.5, (name, zoom)
            assert x + width <= scaled_w + 0.5, (name, zoom)
            assert y + height <= scaled_h + 0.5, (name, zoom)


def test_the_face_lands_where_it_is_asked_to() -> None:
    """The face centre in the output is face_out_x, whatever the zoom."""
    for zoom in (1.0, 1.06, 1.12, 1.18):
        for out_x in (320, 345):
            x, _y, width, _h = _window(zoom, out_x, 260)
            face_in_window = FACE_X * HEADROOM - x
            assert face_in_window / width * SRC_W == pytest.approx(out_x)


def test_the_zoom_ramps_up_over_the_clip() -> None:
    for name, look in PRESETS.items():
        expr = _zoom_expr(7.29, look)
        assert expr
        assert look["zoom_end"] > 1.0, name
        assert look["zoom_end"] <= 1.20, name


def test_the_flash_expression_covers_every_accent() -> None:
    for name, look in PRESETS.items():
        expr = _flash_expr(look)
        for when, amount in look["flashes"]:
            assert f"{when:.3f}" in expr, name
            assert f"{amount:.3f}" in expr, name


def test_the_filter_keeps_the_audio_and_the_resolution() -> None:
    vf = build_filter(7.29, PRESETS["tasteful"], [])
    assert f"scale={SRC_W}:{SRC_H}" in vf
    assert "crop=" in vf
    assert "vignette=" in vf
    assert "unsharp=" in vf
