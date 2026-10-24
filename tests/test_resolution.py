"""Tests for video_pipeline.resolution."""

from __future__ import annotations

from typing import Literal

import pytest
from PIL import Image

from video_pipeline.resolution import (
    SAMPLER_NODE_ID,
    VALID_H3_RESOLUTIONS,
    apply_resolution,
    pixel_budget,
    resolve_resolution,
)

Orientation = Literal["landscape", "portrait", "square"]


def _orientation_for(width: int, height: int) -> Orientation:
    for w, h, oclass in VALID_H3_RESOLUTIONS:
        if w == width and h == height:
            return oclass
    raise AssertionError(f"{width}x{height} not in VALID_H3_RESOLUTIONS")


def _allowed_orientations(
    src_ar: float,
    forced: Orientation | None,
) -> set[Orientation]:
    if forced is not None:
        return {forced}
    if src_ar >= 1.0:
        return {"landscape", "square"}
    return {"portrait"}


def _reference_resolve(
    src_w: int,
    src_h: int,
    length_frames: int = 175,
    max_pixels: int | None = None,
    orientation: str | None = None,
) -> tuple[int, int]:
    """Independent reference implementation of selection invariants."""
    source_ar = src_w / src_h if src_h else 0.0
    budget = max_pixels if max_pixels is not None else pixel_budget(length_frames)
    forced: Orientation | None = None
    if orientation is not None:
        forced = orientation.strip().lower()  # type: ignore[assignment]
    allowed = _allowed_orientations(source_ar, forced)

    candidates: list[tuple[int, int, float]] = []
    for width, height, oclass in VALID_H3_RESOLUTIONS:
        if oclass not in allowed:
            continue
        if width % 16 != 0 or height % 16 != 0:
            continue
        if width * height > budget:
            continue
        cand_ar = width / height
        err = abs(cand_ar - source_ar) / source_ar if source_ar else float("inf")
        candidates.append((width, height, err))

    if not candidates:
        def _largest(orientations: set[Orientation] | None) -> tuple[int, int] | None:
            best = None
            best_area = -1
            for width, height, oclass in VALID_H3_RESOLUTIONS:
                if orientations is not None and oclass not in orientations:
                    continue
                area = width * height
                if area > budget:
                    continue
                if area > best_area:
                    best_area = area
                    best = (width, height)
            return best

        picked = _largest(allowed) or _largest(None)
        if picked is None:
            picked = min(
                ((w, h) for w, h, _ in VALID_H3_RESOLUTIONS),
                key=lambda wh: wh[0] * wh[1],
            )
        return picked

    min_err = min(item[2] for item in candidates)
    tied = [item for item in candidates if item[2] == min_err]
    tied.sort(key=lambda item: (item[0] * item[1], item[0]), reverse=True)
    return tied[0][0], tied[0][1]


def _assert_invariants(
    src_w: int,
    src_h: int,
    result_w: int,
    result_h: int,
    length_frames: int = 175,
    max_pixels: int | None = None,
    orientation: str | None = None,
) -> None:
    source_ar = src_w / src_h
    budget = max_pixels if max_pixels is not None else pixel_budget(length_frames)
    forced: Orientation | None = None
    if orientation is not None:
        forced = orientation.strip().lower()  # type: ignore[assignment]
    allowed = _allowed_orientations(source_ar, forced)

    assert result_w % 16 == 0
    assert result_h % 16 == 0
    assert result_w * result_h <= budget
    ref_w, ref_h = _reference_resolve(
        src_w, src_h, length_frames=length_frames, max_pixels=max_pixels, orientation=orientation
    )
    assert (result_w, result_h) == (ref_w, ref_h)


class TestPixelBudget:
    def test_short_clip_budget(self) -> None:
        assert pixel_budget(100) == 1_400_000

    def test_long_clip_budget(self) -> None:
        assert pixel_budget(175) == 1_000_000
        assert pixel_budget(240) == 1_000_000


class TestResolveResolution:
    def test_portrait_776x1280_length_100(self) -> None:
        resolved = resolve_resolution(776, 1280, length_frames=100)
        assert (resolved.width, resolved.height) == (768, 1280)
        assert resolved.ar_diff_pct <= 2.0
        _assert_invariants(776, 1280, resolved.width, resolved.height, length_frames=100)

    def test_landscape_1920x1080(self) -> None:
        resolved = resolve_resolution(1920, 1080, length_frames=100)
        assert resolved.ar_diff_pct <= 2.0
        assert _orientation_for(resolved.width, resolved.height) in {"landscape", "square"}
        _assert_invariants(1920, 1080, resolved.width, resolved.height, length_frames=100)
        assert (resolved.width, resolved.height) in {(1024, 576), (1280, 720)}

    def test_square_1024x1024(self) -> None:
        resolved = resolve_resolution(1024, 1024, length_frames=100)
        assert resolved.width == resolved.height
        assert (resolved.width, resolved.height) in {(1024, 1024), (1280, 1280)}
        _assert_invariants(1024, 1024, resolved.width, resolved.height, length_frames=100)

    def test_budget_enforced_at_240_frames(self) -> None:
        resolved = resolve_resolution(776, 1280, length_frames=240)
        assert resolved.width * resolved.height <= 1_000_000
        assert (resolved.width, resolved.height) != (1024, 1536)
        _assert_invariants(776, 1280, resolved.width, resolved.height, length_frames=240)

    def test_budget_allows_more_pixels_at_100_frames(self) -> None:
        resolved = resolve_resolution(776, 1280, length_frames=100)
        assert resolved.width * resolved.height <= 1_400_000
        _assert_invariants(776, 1280, resolved.width, resolved.height, length_frames=100)

    def test_max_pixels_override(self) -> None:
        resolved = resolve_resolution(1920, 1080, length_frames=100, max_pixels=600_000)
        assert resolved.width * resolved.height <= 600_000
        _assert_invariants(
            1920, 1080, resolved.width, resolved.height, length_frames=100, max_pixels=600_000
        )

    def test_forced_portrait_on_landscape_source(self) -> None:
        resolved = resolve_resolution(1920, 1080, orientation="portrait")
        assert _orientation_for(resolved.width, resolved.height) == "portrait"
        assert "forced-orientation" in resolved.rule
        assert "override" in resolved.rule.lower()
        _assert_invariants(1920, 1080, resolved.width, resolved.height, orientation="portrait")

    def test_fallback_when_portrait_exceeds_budget(self) -> None:
        resolved = resolve_resolution(776, 1280, max_pixels=900_000)
        assert resolved.rule == "fallback"
        assert resolved.width * resolved.height <= 900_000
        _assert_invariants(776, 1280, resolved.width, resolved.height, max_pixels=900_000)

    def test_all_table_entries_are_multiples_of_16(self) -> None:
        for width, height, _ in VALID_H3_RESOLUTIONS:
            assert width % 16 == 0
            assert height % 16 == 0


class TestApplyResolution:
    def test_writes_sampler_node(self) -> None:
        workflow = {
            "7": {"inputs": {"prompt": "x", "width": 1, "height": 2, "length": 175}},
            "8": {"inputs": {"noise_seed": 1}},
        }
        apply_resolution(workflow, 1280, 720)
        assert workflow[SAMPLER_NODE_ID]["inputs"]["width"] == 1280
        assert workflow[SAMPLER_NODE_ID]["inputs"]["height"] == 720
        assert workflow["8"]["inputs"]["noise_seed"] == 1


class TestResolutionCLI:
    def test_cli_missing_image_exits_zero(self, tmp_path, capsys) -> None:
        from video_pipeline.resolution import _cli

        missing = tmp_path / "missing.png"
        code = _cli(["--image", str(missing)])
        assert code == 0
        out = capsys.readouterr().out
        assert "chosen:" in out
        assert "pixel budget:" in out

    def test_cli_reads_image(self, tmp_path, capsys) -> None:
        from video_pipeline.resolution import _cli

        image = tmp_path / "src.png"
        Image.new("RGB", (776, 1280), color="blue").save(image)
        code = _cli(["--image", str(image), "--length", "100"])
        assert code == 0
        out = capsys.readouterr().out
        assert "776x1280" in out
        assert "768x1280" in out
