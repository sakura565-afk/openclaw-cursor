"""Unit tests for the FreeVideo canvas resolver and the product-shot post chain."""

from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

from video_pipeline.resolution import (
    FREEVIDEO_MIN_DIM,
    freevideo_max_pixels,
    resolve_freevideo_resolution,
)


def test_freevideo_max_pixels_matches_the_measured_good_canvas() -> None:
    """754k px (736x1024) rendered fine with ~15.7 GB of free VRAM."""
    budget = freevideo_max_pixels(15.7)
    assert 754_000 <= budget <= 800_000


def test_freevideo_max_pixels_rejects_the_ram_guard_case() -> None:
    """1.04M px needed 20.50 GiB of working RAM and tripped the 20.38 GiB guard."""
    assert freevideo_max_pixels(15.7) < 1_038_000


def test_freevideo_max_pixels_is_zero_without_headroom() -> None:
    assert freevideo_max_pixels(13.0) == 0


def test_resolver_keeps_the_measured_chair_canvas() -> None:
    assert resolve_freevideo_resolution(960, 1280, 7.29, 15.7) == (768, 1024)


def test_resolver_keeps_the_measured_orbit_canvas() -> None:
    assert resolve_freevideo_resolution(916, 1280, 7.29, 15.7) == (736, 1024)


def test_resolver_shrinks_the_sofa_canvas_that_tripped_the_ram_guard() -> None:
    """1280x853 AR at 1248x832 blew the RAM budget; the resolver must pick less."""
    width, height = resolve_freevideo_resolution(1280, 853, 7.29, 15.7)
    assert (width, height) != (1248, 832)
    assert width % 32 == 0 and height % 32 == 0


def test_resolver_preserves_aspect_ratio() -> None:
    width, height = resolve_freevideo_resolution(916, 1280, 7.29, 15.7)
    source_ar = 916 / 1280
    assert abs((width / height) - source_ar) < 0.01


def test_resolver_sides_are_multiples_of_32() -> None:
    width, height = resolve_freevideo_resolution(1000, 1333, 7.29, 15.7)
    assert width % 32 == 0 and height % 32 == 0


def test_resolver_falls_back_to_the_minimum_without_vram() -> None:
    width, height = resolve_freevideo_resolution(916, 1280, 7.29, 13.0)
    assert width >= FREEVIDEO_MIN_DIM and height >= FREEVIDEO_MIN_DIM


def test_resolver_rejects_broken_sources() -> None:
    with pytest.raises(ValueError):
        resolve_freevideo_resolution(0, 100)


def test_product_shot_cli_exposes_the_post_chain() -> None:
    spec = importlib.util.spec_from_file_location(
        "product_shot_cli",
        Path(__file__).resolve().parents[1] / "scripts" / "product_shot.py",
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    args = module.build_parser().parse_args(
        [
            "--image", "a.png",
            "--width", "736",
            "--height", "1024",
            "--prompt", "p.txt",
            "--out", "o.mp4",
            "--interpolate", "48",
            "--upscale", "seedvr2",
            "--upscale-height", "1920",
        ]
    )
    assert args.interpolate == 48.0
    assert args.upscale == "seedvr2"
    assert args.upscale_height == 1920
