"""Tests for video_pipeline.assembler."""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path
from unittest import mock

import pytest

from video_pipeline.assembler import (
    AssemblySpec,
    ClipSpec,
    build_concat_demuxer_argv,
    build_normalize_argv,
    concat_clips,
    parse_ar,
    probe,
    scale_pad_filter,
    target_resolution,
)


def _ffmpeg_available() -> bool:
    return shutil.which("ffmpeg") is not None and shutil.which("ffprobe") is not None


class TestParseAr:
    def test_ratio_strings(self) -> None:
        assert parse_ar("16:9") == pytest.approx(16 / 9)
        assert parse_ar("9:16") == pytest.approx(9 / 16)
        assert parse_ar("1:1") == pytest.approx(1.0)

    def test_float_string_and_number(self) -> None:
        assert parse_ar("0.5625") == pytest.approx(0.5625)
        assert parse_ar(1.777) == pytest.approx(1.777)


class TestTargetResolution:
    def test_even_dimensions(self) -> None:
        clips = [{"width": 101, "height": 99}]
        w, h = target_resolution(clips, target_ar=1.0)
        assert w % 2 == 0
        assert h % 2 == 0

    def test_default_uses_max_aspect_ratio(self) -> None:
        clips = [
            {"width": 1280, "height": 720},
            {"width": 720, "height": 1280},
        ]
        w, h = target_resolution(clips)
        assert w / h == pytest.approx(1280 / 720, rel=0.02)
        assert w >= 1280
        assert h >= 1280

    def test_explicit_target_ar(self) -> None:
        clips = [{"width": 200, "height": 200}]
        w, h = target_resolution(clips, target_ar=9 / 16)
        assert w / h == pytest.approx(9 / 16, rel=0.02)
        assert w >= 200


class TestArgvConstruction:
    def test_scale_pad_filter_contains_expected_ops(self) -> None:
        vf = scale_pad_filter(1920, 1080, fps=24)
        assert "force_original_aspect_ratio=decrease" in vf
        assert "pad=1920:1080" in vf
        assert "fps=24" in vf

    def test_normalize_argv_is_list_without_shell_metacharacters(self) -> None:
        src = Path("/tmp/a.mp4")
        dst = Path("/tmp/b.mp4")
        argv = build_normalize_argv(
            src,
            dst,
            width=1280,
            height=720,
            fps=24,
            has_audio=True,
            ensure_audio=True,
        )
        assert isinstance(argv, list)
        assert argv[0] == "ffmpeg"
        assert "-y" in argv
        vf = argv[argv.index("-vf") + 1]
        assert "force_original_aspect_ratio=decrease" in vf
        assert "pad=1280:720" in vf
        for token in argv:
            assert ";" not in token
            assert "|" not in token
            assert "&" not in token

    def test_concat_demuxer_argv(self) -> None:
        argv = build_concat_demuxer_argv(Path("/tmp/list.txt"), Path("/tmp/out.mp4"))
        assert argv[0] == "ffmpeg"
        assert "-y" in argv
        assert "concat" in argv
        assert "-c" in argv and "copy" in argv


def test_probe_parses_ffprobe_json(tmp_path: Path) -> None:
    video = tmp_path / "clip.mp4"
    video.write_bytes(b"x")
    payload = {
        "streams": [
            {
                "codec_type": "video",
                "width": 640,
                "height": 480,
                "avg_frame_rate": "24/1",
            },
            {"codec_type": "audio", "codec_name": "aac"},
        ],
        "format": {"duration": "2.5"},
    }
    proc = mock.MagicMock()
    proc.returncode = 0
    proc.stdout = json.dumps(payload)
    proc.stderr = ""
    with mock.patch("video_pipeline.assembler.subprocess.run", return_value=proc):
        info = probe(video)
    assert info["width"] == 640
    assert info["height"] == 480
    assert info["duration"] == 2.5
    assert info["fps"] == 24.0
    assert info["has_audio"] is True


@pytest.mark.skipif(not _ffmpeg_available(), reason="ffmpeg not installed")
class TestConcatIntegration:
    def _make_clip(self, path: Path, size: str, duration: float, freq: int) -> None:
        cmd = [
            "ffmpeg",
            "-y",
            "-f",
            "lavfi",
            "-i",
            f"testsrc=size={size}:rate=24:duration={duration}",
            "-f",
            "lavfi",
            "-i",
            f"sine=frequency={freq}:duration={duration}",
            "-c:v",
            "libx264",
            "-pix_fmt",
            "yuv420p",
            "-c:a",
            "aac",
            "-shortest",
            str(path),
        ]
        subprocess.run(cmd, check=True, capture_output=True, text=True)

    def test_concat_preserves_audio_and_duration(self, tmp_path: Path) -> None:
        a = tmp_path / "a.mp4"
        b = tmp_path / "b.mp4"
        c = tmp_path / "c.mp4"
        self._make_clip(a, "320x240", 1.0, 440)
        self._make_clip(b, "640x480", 1.5, 880)
        self._make_clip(c, "480x640", 0.8, 660)
        out = tmp_path / "assembled.mp4"

        clips = [ClipSpec(a), ClipSpec(b), ClipSpec(c)]
        concat_clips(clips, out)

        assert out.exists()
        info_out = probe(out)
        info_a, info_b, info_c = probe(a), probe(b), probe(c)
        expected = info_a["duration"] + info_b["duration"] + info_c["duration"]
        assert abs(info_out["duration"] - expected) <= 0.3
        assert info_out["has_audio"] is True

        tw, th = target_resolution([info_a, info_b, info_c])
        assert info_out["width"] == tw
        assert info_out["height"] == th


def test_assemble_spec_dataclass(tmp_path: Path) -> None:
    spec = AssemblySpec(
        clips=[ClipSpec(tmp_path / "a.mp4")],
        out_path=tmp_path / "out.mp4",
        interpolate=False,
    )
    assert spec.target_fps is None
