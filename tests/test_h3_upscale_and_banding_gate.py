"""H3 upscale dimensions and video_pipeline banding quality gate tests."""

from __future__ import annotations

from pathlib import Path
from unittest import mock

import pytest

from video_pipeline.directors.h3 import FORMAT_MAP, upscale_target_dimensions
from video_pipeline.quality import QualityGate


class TestH3FormatMap:
    def test_format_map_dimensions_even_and_mod16(self) -> None:
        for fmt, entry in FORMAT_MAP.items():
            w, h = entry["width"], entry["height"]
            assert w % 2 == 0, f"{fmt} width {w} is odd"
            assert h % 2 == 0, f"{fmt} height {h} is odd"
            assert w % 16 == 0, f"{fmt} width {w} not divisible by 16"
            assert h % 16 == 0, f"{fmt} height {h} not divisible by 16"

    def test_upscale_target_dimensions_even_for_all_formats(self) -> None:
        for fmt in FORMAT_MAP:
            w, h = upscale_target_dimensions(fmt)
            assert w % 2 == 0
            assert h % 2 == 0

    def test_upscale_target_rounds_odd_table_values(self) -> None:
        patched = dict(FORMAT_MAP["9:16"])
        patched["height"] = 1119
        with mock.patch.dict(FORMAT_MAP, {"9:16": patched}):
            w, h = upscale_target_dimensions("9:16")
        assert w == 736
        assert h == 1120


def _banding_report(
    verdict: str,
    score: float = 0.0,
    band_count: int = 0,
) -> dict[str, object]:
    return {"verdict": verdict, "score": score, "band_count": band_count}


class TestCheckBandingGate:
    @pytest.fixture()
    def video(self, tmp_path: Path) -> Path:
        path = tmp_path / "clip.mp4"
        path.write_bytes(b"fake")
        return path

    def _check(self, video: Path, report: dict[str, object]) -> object:
        gate = QualityGate()
        with mock.patch(
            "scripts.banding_detector.analyze_video",
            return_value=report,
        ):
            return gate.check_banding(video, max_score=25.0)

    def test_geometry_verdict_passes(self, video: Path) -> None:
        result = self._check(video, _banding_report("geometry", score=0.0, band_count=3))
        assert result.passed
        assert "exceeds max" not in result.message.lower()
        assert "geometry" in result.message

    def test_clean_verdict_passes(self, video: Path) -> None:
        result = self._check(video, _banding_report("clean", score=0.0))
        assert result.passed

    def test_banding_verdict_fails(self, video: Path) -> None:
        result = self._check(video, _banding_report("banding", score=40.0, band_count=2))
        assert not result.passed
        assert "defect" in result.message.lower() or "banding" in result.message.lower()
        assert "exceeds max" not in result.message.lower()

    def test_periodic_verdict_fails(self, video: Path) -> None:
        result = self._check(video, _banding_report("periodic", score=50.0, band_count=1))
        assert not result.passed
        assert "periodic" in result.message

    def test_high_score_clean_verdict_fails(self, video: Path) -> None:
        result = self._check(video, _banding_report("clean", score=30.0))
        assert not result.passed
        assert "exceeds max" in result.message.lower()

    def test_geometry_pass_message_does_not_claim_exceeds_max(self, video: Path) -> None:
        result = self._check(video, _banding_report("geometry", score=0.0, band_count=1))
        assert result.passed
        assert "exceeds max" not in result.message
