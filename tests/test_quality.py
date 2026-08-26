"""Tests for video_pipeline.quality."""

from __future__ import annotations

import json
from pathlib import Path
from unittest import mock

import pytest
from PIL import Image

from video_pipeline.config import QualityGates
from video_pipeline.quality import QualityGate


def _make_ffprobe_proc(payload: dict, returncode: int = 0) -> mock.MagicMock:
    """Build a mock subprocess.CompletedProcess for ffprobe."""
    proc = mock.MagicMock()
    proc.returncode = returncode
    proc.stdout = json.dumps(payload)
    proc.stderr = ""
    return proc


class TestQualityGateAR:
    def test_ar_drift_pass_0_5_percent(self, tmp_path: Path) -> None:
        source = tmp_path / "source.jpg"
        Image.new("RGB", (1000, 1000), color="red").save(source)
        video = tmp_path / "video.mp4"
        video.write_bytes(b"fake")

        payload = {
            "streams": [{"codec_type": "video", "codec_name": "h264", "width": 1005, "height": 1000}],
            "format": {"duration": "7.0"},
        }

        gate = QualityGate()
        with mock.patch("video_pipeline.quality.subprocess.run", return_value=_make_ffprobe_proc(payload)):
            result = gate.check_ar(video, source, max_drift=0.01)
        assert result.passed
        assert result.actual < 0.01

    def test_ar_drift_pass_1_percent(self, tmp_path: Path) -> None:
        source = tmp_path / "source.jpg"
        Image.new("RGB", (1000, 1000), color="red").save(source)
        video = tmp_path / "video.mp4"
        video.write_bytes(b"fake")

        payload = {
            "streams": [{"codec_type": "video", "codec_name": "h264", "width": 1010, "height": 1000}],
            "format": {"duration": "7.0"},
        }

        gate = QualityGate()
        with mock.patch("video_pipeline.quality.subprocess.run", return_value=_make_ffprobe_proc(payload)):
            result = gate.check_ar(video, source, max_drift=0.02)
        assert result.passed

    def test_ar_drift_pass_2_percent(self, tmp_path: Path) -> None:
        source = tmp_path / "source.jpg"
        Image.new("RGB", (1000, 1000), color="red").save(source)
        video = tmp_path / "video.mp4"
        video.write_bytes(b"fake")

        payload = {
            "streams": [{"codec_type": "video", "codec_name": "h264", "width": 1020, "height": 1000}],
            "format": {"duration": "7.0"},
        }

        gate = QualityGate()
        with mock.patch("video_pipeline.quality.subprocess.run", return_value=_make_ffprobe_proc(payload)):
            result = gate.check_ar(video, source, max_drift=0.03)
        assert result.passed

    def test_ar_drift_fail_5_percent(self, tmp_path: Path) -> None:
        source = tmp_path / "source.jpg"
        Image.new("RGB", (1000, 1000), color="red").save(source)
        video = tmp_path / "video.mp4"
        video.write_bytes(b"fake")

        payload = {
            "streams": [{"codec_type": "video", "codec_name": "h264", "width": 1050, "height": 1000}],
            "format": {"duration": "7.0"},
        }

        gate = QualityGate()
        with mock.patch("video_pipeline.quality.subprocess.run", return_value=_make_ffprobe_proc(payload)):
            result = gate.check_ar(video, source, max_drift=0.03)
        assert not result.passed
        assert result.actual > 0.03


class TestQualityGateDuration:
    def test_duration_pass(self, tmp_path: Path) -> None:
        video = tmp_path / "video.mp4"
        video.write_bytes(b"fake")
        payload = {
            "streams": [{"codec_type": "video", "codec_name": "h264", "width": 1280, "height": 720}],
            "format": {"duration": "7.2"},
        }
        gate = QualityGate()
        with mock.patch("video_pipeline.quality.subprocess.run", return_value=_make_ffprobe_proc(payload)):
            result = gate.check_duration(video, target_sec=7.0, tolerance=0.5)
        assert result.passed

    def test_duration_fail(self, tmp_path: Path) -> None:
        video = tmp_path / "video.mp4"
        video.write_bytes(b"fake")
        payload = {
            "streams": [{"codec_type": "video", "codec_name": "h264", "width": 1280, "height": 720}],
            "format": {"duration": "5.0"},
        }
        gate = QualityGate()
        with mock.patch("video_pipeline.quality.subprocess.run", return_value=_make_ffprobe_proc(payload)):
            result = gate.check_duration(video, target_sec=7.0, tolerance=0.5)
        assert not result.passed


class TestQualityGateResolution:
    def test_resolution_pass(self, tmp_path: Path) -> None:
        video = tmp_path / "video.mp4"
        video.write_bytes(b"fake")
        payload = {
            "streams": [{"codec_type": "video", "codec_name": "h264", "width": 1280, "height": 720}],
            "format": {"duration": "7.0"},
        }
        gate = QualityGate()
        with mock.patch("video_pipeline.quality.subprocess.run", return_value=_make_ffprobe_proc(payload)):
            result = gate.check_resolution(video, min_h=720)
        assert result.passed

    def test_resolution_fail(self, tmp_path: Path) -> None:
        video = tmp_path / "video.mp4"
        video.write_bytes(b"fake")
        payload = {
            "streams": [{"codec_type": "video", "codec_name": "h264", "width": 640, "height": 480}],
            "format": {"duration": "7.0"},
        }
        gate = QualityGate()
        with mock.patch("video_pipeline.quality.subprocess.run", return_value=_make_ffprobe_proc(payload)):
            result = gate.check_resolution(video, min_h=720)
        assert not result.passed


class TestQualityGateCodec:
    def test_codec_pass(self, tmp_path: Path) -> None:
        video = tmp_path / "video.mp4"
        video.write_bytes(b"fake")
        payload = {
            "streams": [{"codec_type": "video", "codec_name": "h264", "width": 1280, "height": 720}],
            "format": {"duration": "7.0"},
        }
        gate = QualityGate()
        with mock.patch("video_pipeline.quality.subprocess.run", return_value=_make_ffprobe_proc(payload)):
            result = gate.check_codec(video, expected="h264")
        assert result.passed

    def test_codec_fail(self, tmp_path: Path) -> None:
        video = tmp_path / "video.mp4"
        video.write_bytes(b"fake")
        payload = {
            "streams": [{"codec_type": "video", "codec_name": "hevc", "width": 1280, "height": 720}],
            "format": {"duration": "7.0"},
        }
        gate = QualityGate()
        with mock.patch("video_pipeline.quality.subprocess.run", return_value=_make_ffprobe_proc(payload)):
            result = gate.check_codec(video, expected="h264")
        assert not result.passed


class TestQualityReport:
    def test_check_all_aggregates(self, tmp_path: Path, mock_ffprobe_success: dict) -> None:
        source = tmp_path / "source.jpg"
        Image.new("RGB", (1280, 720), color="blue").save(source)
        video = tmp_path / "video.mp4"
        video.write_bytes(b"fake")

        gate = QualityGate()
        gates = QualityGates(max_ar_drift=0.05, min_resolution_h=720, expected_codec="h264")

        with mock.patch("video_pipeline.quality.subprocess.run", return_value=_make_ffprobe_proc(mock_ffprobe_success)):
            report = gate.check_all(video, source, gates, target_duration_sec=7.0)

        assert report.overall_passed
        assert len(report.checks) == 4

    def test_check_all_fail_on_codec(self, tmp_path: Path) -> None:
        source = tmp_path / "source.jpg"
        Image.new("RGB", (1280, 720), color="blue").save(source)
        video = tmp_path / "video.mp4"
        video.write_bytes(b"fake")

        payload = {
            "streams": [{"codec_type": "video", "codec_name": "vp9", "width": 1280, "height": 720}],
            "format": {"duration": "7.0"},
        }
        gate = QualityGate()
        gates = QualityGates()

        with mock.patch("video_pipeline.quality.subprocess.run", return_value=_make_ffprobe_proc(payload)):
            report = gate.check_all(video, source, gates)

        assert not report.overall_passed
        failed_names = [c.name for c in report.checks if not c.passed]
        assert "codec" in failed_names
