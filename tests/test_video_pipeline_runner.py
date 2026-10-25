"""Runner integration tests with stubbed director, notifier, and assembler."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from unittest import mock

import pytest

from video_pipeline.assembler import AssemblyError, ClipSpec
from video_pipeline.config import (
    AssemblyConfig,
    DeliveryConfig,
    InterpolationConfig,
    OutputSpec,
    PipelineConfig,
    QualityGates,
    RecoveryConfig,
)
from video_pipeline.director_base import JobSpec, VideoResult
from video_pipeline.quality import CheckResult, QualityReport
from video_pipeline.runner import run_pipeline
from video_pipeline.state import StateManager


@dataclass
class _FakeJob:
    source_image: Path
    output_spec: OutputSpec
    workflow: dict
    output_path: Path
    prompt_text: str
    width: int
    height: int
    length_frames: int


class FakeDirector:
    def __init__(self, video_path: Path) -> None:
        self.video_path = video_path
        self.upscale_path = video_path

    def prepare(self, source_image: Path, output_spec: OutputSpec) -> _FakeJob:
        return _FakeJob(
            source_image=source_image,
            output_spec=output_spec,
            workflow={},
            output_path=source_image.with_suffix(".mp4"),
            prompt_text="",
            width=512,
            height=512,
            length_frames=24,
        )

    def submit(self, job: _FakeJob) -> str:
        return "prompt-1"

    def poll_until_done_with_job(self, prompt_id: str, timeout_sec: int, job: _FakeJob) -> VideoResult:
        return VideoResult(
            video_path=self.video_path,
            prompt_id=prompt_id,
            duration_sec=7.0,
            render_sec=3.0,
        )

    def upscale(self, video_path: Path, target_format: str) -> Path:
        return self.upscale_path


class FakeNotifier:
    def __init__(self) -> None:
        self.videos: list[tuple[Path, str]] = []

    def send_video(self, path: Path, caption: str, chat_id: int) -> None:
        self.videos.append((path, caption))

    def send_progress(self, text: str, chat_id: int) -> None:
        pass

    def send_text(self, text: str, chat_id: int) -> None:
        pass


def _base_config(tmp_path: Path, **overrides: object) -> PipelineConfig:
    source = tmp_path / "src"
    source.mkdir()
    image = source / "photo.jpg"
    image.write_bytes(b"fake")

    out_dir = tmp_path / "out"
    out_dir.mkdir()
    video = out_dir / "clip.mp4"
    video.write_bytes(b"mp4")

    defaults = {
        "project": "test",
        "source_dir": source,
        "outputs": [OutputSpec(format="1:1", duration_sec=7)],
        "delivery": DeliveryConfig(telegram_chat=1),
        "recovery": RecoveryConfig(state_file=tmp_path / "state.json", resume_from_state=False),
        "output_dir": out_dir,
        "quality_gates": QualityGates(include_banding=True, banding_max_score=25.0),
    }
    defaults.update(overrides)
    return PipelineConfig.model_validate(defaults), video


def _passing_report() -> QualityReport:
    report = QualityReport()
    report.add(
        CheckResult(
            name="aspect_ratio",
            passed=True,
            actual=0.0,
            expected=0.03,
            message="ok",
        )
    )
    return report


@pytest.fixture
def patch_pipeline_deps(tmp_path: Path):
    config, video_path = _base_config(tmp_path)
    director = FakeDirector(video_path)
    notifier = FakeNotifier()

    with (
        mock.patch("video_pipeline.runner._get_director", return_value=director),
        mock.patch("video_pipeline.runner.TelegramNotifier", return_value=notifier),
        mock.patch("video_pipeline.runner.QualityGate") as quality_cls,
        mock.patch("video_pipeline.runner.assembler") as asm_mod,
    ):
        quality = quality_cls.return_value
        quality.check_all.return_value = _passing_report()
        quality.check_ar.return_value = CheckResult(
            name="aspect_ratio",
            passed=True,
            actual=0.01,
            expected=0.03,
            message="ok",
        )
        quality.check_duration.return_value = CheckResult(
            name="duration",
            passed=True,
            actual=7.0,
            expected=7.0,
            message="ok",
        )
        asm_mod.target_resolution.return_value = (1080, 1080)
        asm_mod.concat_clips.return_value = config.output_dir / "assembled.mp4"
        asm_mod.interpolate.side_effect = lambda *a, **k: a[1]
        yield config, notifier, quality, asm_mod, video_path


def test_check_all_called_with_banding_when_gate_enabled(patch_pipeline_deps) -> None:
    config, _, quality, _, _ = patch_pipeline_deps
    exit_code = run_pipeline(config)
    assert exit_code == 0
    quality.check_all.assert_called()
    _, kwargs = quality.check_all.call_args
    assert kwargs.get("include_banding") is True
    assert kwargs.get("banding_max_score") == 25.0


def test_interpolation_calls_assembler_and_keeps_file_on_assembly_error(
    tmp_path: Path,
) -> None:
    config, video_path = _base_config(
        tmp_path,
        interpolation=InterpolationConfig(enabled=True, target_fps=48.0),
    )
    director = FakeDirector(video_path)
    notifier = FakeNotifier()

    with (
        mock.patch("video_pipeline.runner._get_director", return_value=director),
        mock.patch("video_pipeline.runner.TelegramNotifier", return_value=notifier),
        mock.patch("video_pipeline.runner.QualityGate") as quality_cls,
        mock.patch("video_pipeline.runner.assembler") as asm_mod,
    ):
        quality = quality_cls.return_value
        quality.check_all.return_value = _passing_report()
        quality.check_ar.return_value = CheckResult(
            name="aspect_ratio",
            passed=True,
            actual=0.0,
            expected=0.03,
            message="ok",
        )
        asm_mod.interpolate.side_effect = AssemblyError("ComfyUI down")

        assert run_pipeline(config) == 0
        asm_mod.interpolate.assert_called_once()
        assert notifier.videos[0][0] == video_path


def test_interpolated_clip_passes_pre_interp_file_as_audio_path(tmp_path: Path) -> None:
    config, video_path = _base_config(
        tmp_path,
        interpolation=InterpolationConfig(enabled=True, target_fps=48.0),
    )
    upscaled = video_path.with_name("clip_upscaled.mp4")
    upscaled.write_bytes(b"up")
    director = FakeDirector(video_path)
    director.upscale_path = upscaled
    interp_out = upscaled.with_name("clip_upscaled_interp.mp4")
    interp_out.write_bytes(b"interp")

    with (
        mock.patch("video_pipeline.runner._get_director", return_value=director),
        mock.patch("video_pipeline.runner.TelegramNotifier", return_value=FakeNotifier()),
        mock.patch("video_pipeline.runner.QualityGate") as quality_cls,
        mock.patch("video_pipeline.runner.assembler") as asm_mod,
    ):
        quality = quality_cls.return_value
        quality.check_all.return_value = _passing_report()
        quality.check_ar.return_value = CheckResult(
            name="aspect_ratio",
            passed=True,
            actual=0.0,
            expected=0.03,
            message="ok",
        )
        quality.check_duration.return_value = CheckResult(
            name="duration",
            passed=True,
            actual=7.0,
            expected=7.0,
            message="ok",
        )
        asm_mod.interpolate.return_value = interp_out
        config.assembly = AssemblyConfig(enabled=True, output_name="final_cut")
        assembled = config.output_dir / "final_cut.mp4"
        assembled.write_bytes(b"asm")
        asm_mod.concat_clips.return_value = assembled
        asm_mod.target_resolution.return_value = (1080, 1080)

        assert run_pipeline(config) == 0
        clip_args = asm_mod.concat_clips.call_args[0][0]
        assert len(clip_args) == 1
        assert clip_args[0].path == interp_out
        assert clip_args[0].audio_path == upscaled


def test_interpolation_success_reruns_duration_check(tmp_path: Path) -> None:
    config, video_path = _base_config(
        tmp_path,
        interpolation=InterpolationConfig(enabled=True),
    )
    director = FakeDirector(video_path)
    interp_out = video_path.with_name("clip_interp.mp4")
    interp_out.write_bytes(b"interp")

    with (
        mock.patch("video_pipeline.runner._get_director", return_value=director),
        mock.patch("video_pipeline.runner.TelegramNotifier", return_value=FakeNotifier()),
        mock.patch("video_pipeline.runner.QualityGate") as quality_cls,
        mock.patch("video_pipeline.runner.assembler") as asm_mod,
    ):
        quality = quality_cls.return_value
        quality.check_all.return_value = _passing_report()
        quality.check_ar.return_value = CheckResult(
            name="aspect_ratio",
            passed=True,
            actual=0.0,
            expected=0.03,
            message="ok",
        )
        quality.check_duration.return_value = CheckResult(
            name="duration",
            passed=True,
            actual=7.0,
            expected=7.0,
            message="ok",
        )
        asm_mod.interpolate.return_value = interp_out

        assert run_pipeline(config) == 0
        quality.check_duration.assert_called_once_with(interp_out, 7.0)


def test_assembled_quality_gate_uses_expected_ar_not_source_image(
    patch_pipeline_deps,
) -> None:
    config, _, quality, asm_mod, _ = patch_pipeline_deps
    config.assembly = AssemblyConfig(enabled=True, output_name="final_cut")
    assembled = config.output_dir / "final_cut.mp4"
    assembled.write_bytes(b"asm")
    asm_mod.concat_clips.return_value = assembled
    asm_mod.target_resolution.return_value = (1080, 1920)

    assert run_pipeline(config) == 0

    asm_calls = [
        call
        for call in quality.check_all.call_args_list
        if call.args and call.args[0] == assembled
    ]
    assert len(asm_calls) == 1
    asm_call = asm_calls[0]
    assert asm_call.kwargs.get("expected_ar") == pytest.approx(1080 / 1920)
    assert asm_call.args[1] is None
    for call in quality.check_all.call_args_list:
        if call.args and call.args[0] == assembled:
            assert call.kwargs.get("expected_ar") is not None
            assert call.args[1] is None


def test_assembly_concat_and_deliver_assembled(patch_pipeline_deps, tmp_path: Path) -> None:
    config, notifier, quality, asm_mod, video_path = patch_pipeline_deps
    config.assembly = AssemblyConfig(enabled=True, output_name="final_cut")
    assembled = config.output_dir / "final_cut.mp4"
    assembled.write_bytes(b"asm")
    asm_mod.concat_clips.return_value = assembled

    assert run_pipeline(config) == 0
    asm_mod.concat_clips.assert_called_once()
    clip_args = asm_mod.concat_clips.call_args[0][0]
    assert len(clip_args) == 1
    assert isinstance(clip_args[0], ClipSpec)
    delivered_paths = [p for p, _ in notifier.videos]
    assert assembled in delivered_paths


def test_assembly_deliver_per_clip_false_suppresses_clip_delivery(
    patch_pipeline_deps,
) -> None:
    config, notifier, _, asm_mod, _ = patch_pipeline_deps
    config.assembly = AssemblyConfig(
        enabled=True,
        deliver_per_clip=False,
        deliver_assembled=True,
    )
    assembled = config.output_dir / "assembled.mp4"
    assembled.write_bytes(b"asm")
    asm_mod.concat_clips.return_value = assembled

    assert run_pipeline(config) == 0
    assert len(notifier.videos) == 1
    assert notifier.videos[0][0] == assembled
