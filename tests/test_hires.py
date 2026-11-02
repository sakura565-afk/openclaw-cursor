"""Unit tests for the reusable hi-res post-processing chain (no ComfyUI/network)."""

from __future__ import annotations

import os
from pathlib import Path
from unittest import mock

import pytest

from video_pipeline import hires
from video_pipeline.hires import HiresResult, build_hires


class FakeRunner:
    def __init__(self, src: Path, out: Path) -> None:
        self.src = src
        self.out = out
        self.calls: list[tuple] = []

    def probe(self, path: Path) -> dict:
        self.calls.append(("probe", path))
        if path == self.src:
            return {
                "width": 704,
                "height": 1056,
                "fps": 24.0,
                "duration": 7.25,
                "has_audio": True,
            }
        return {
            "width": 1088,
            "height": 1632,
            "fps": 48.0,
            "duration": 7.25,
            "has_audio": True,
        }

    def upscale(
        self,
        src: Path,
        out: Path,
        width: int,
        height: int,
        comfy_url: str,
    ) -> str:
        self.calls.append(("upscale", src, out, width, height, comfy_url))
        return "seedvr2"

    def interpolate(self, src: Path, out: Path, fps: float, comfy_url: str) -> Path:
        self.calls.append(("interpolate", src, out, fps, comfy_url))
        return out

    def mux(self, video: Path, audio_source: Path, out: Path) -> Path:
        self.calls.append(("mux", video, audio_source, out))
        return out


def test_build_hires_runs_upscale_interpolate_then_mux(tmp_path: Path) -> None:
    src = tmp_path / "in.mp4"
    out = tmp_path / "out.mp4"
    runner = FakeRunner(src, out)

    result = build_hires(src, out, height=1632, runner=runner)

    stages = [call[0] for call in runner.calls]
    assert stages == ["probe", "upscale", "interpolate", "mux", "probe"]
    upscale = runner.calls[1]
    assert upscale[1] == src
    assert upscale[3:5] == (1088, 1632)
    interpolate = runner.calls[2]
    assert interpolate[1] == upscale[2]
    assert interpolate[3] == 48.0
    mux = runner.calls[3]
    assert mux[1] == interpolate[2]
    assert mux[2] == src
    assert mux[3] == out
    assert result.out == out
    assert result.width == 1088
    assert result.height == 1632
    assert result.fps == 48.0
    assert result.duration == 7.25
    assert result.upscale_backend == "seedvr2"


def test_strict_sets_no_fallback_and_upscale_failure_is_not_hidden(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("UPSCALE_NO_FALLBACK", raising=False)
    src = tmp_path / "in.mp4"
    out = tmp_path / "out.mp4"
    runner = FakeRunner(src, out)

    def fail_upscale(*args: object) -> str:
        assert os.environ["UPSCALE_NO_FALLBACK"] == "1"
        raise RuntimeError("SeedVR2: not enough VRAM")

    runner.upscale = fail_upscale  # type: ignore[method-assign]

    with pytest.raises(RuntimeError, match="not enough VRAM"):
        build_hires(src, out, height=1632, runner=runner)

    assert [call[0] for call in runner.calls] == ["probe"]
    assert "UPSCALE_NO_FALLBACK" not in os.environ


@pytest.mark.parametrize("height", [0, -1620])
def test_invalid_target_height_fails_before_any_media_work(
    tmp_path: Path, height: int
) -> None:
    src = tmp_path / "in.mp4"
    out = tmp_path / "out.mp4"
    runner = FakeRunner(src, out)

    with pytest.raises(ValueError, match="height.*positive"):
        build_hires(src, out, height=height, runner=runner)

    assert runner.calls == []


def test_strict_rejects_a_returned_lanczos_result(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("UPSCALE_NO_FALLBACK", raising=False)
    src = tmp_path / "in.mp4"
    out = tmp_path / "out.mp4"
    runner = FakeRunner(src, out)

    def return_lanczos(*args: object) -> str:
        runner.calls.append(("upscale", *args))
        return "lanczos"

    runner.upscale = return_lanczos  # type: ignore[method-assign]

    with pytest.raises(RuntimeError, match="lanczos.*strict"):
        build_hires(src, out, height=1632, runner=runner)

    assert [call[0] for call in runner.calls] == ["probe", "upscale"]


def test_optional_quality_gate_runs_last_and_is_in_result(tmp_path: Path) -> None:
    src = tmp_path / "in.mp4"
    out = tmp_path / "out.mp4"
    runner = FakeRunner(src, out)

    def gate(path: Path) -> str:
        runner.calls.append(("quality_gate", path))
        return "quality-ok"

    runner.quality_gate = gate  # type: ignore[attr-defined]

    result = build_hires(src, out, height=1632, runner=runner)

    assert runner.calls[-1] == ("quality_gate", out)
    assert result.quality == "quality-ok"


def test_cli_forwards_no_strict_and_prints_actual_backend(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    src = tmp_path / "in.mp4"
    out = tmp_path / "out.mp4"
    expected = HiresResult(
        src=src,
        out=out,
        width=1088,
        height=1632,
        fps=48.0,
        duration=7.25,
        has_audio=True,
        upscale_backend="seedvr2",
    )

    with mock.patch.object(hires, "build_hires", return_value=expected) as build:
        assert hires.main(["--src", str(src), "--out", str(out), "--no-strict"]) == 0

    build.assert_called_once_with(
        src,
        out,
        height=1620,
        width=0,
        interpolate_fps=48.0,
        comfy_url="http://127.0.0.1:8188",
        strict=False,
    )
    assert '"upscale_backend": "seedvr2"' in capsys.readouterr().out
