"""Offline tests for the resumable H3 last-frame chain pipeline."""

from __future__ import annotations

import json
import shutil
import subprocess
from dataclasses import replace
from pathlib import Path

import pytest

from scripts.h3_chain_pipeline_kimi.pipeline import (
    ChainConfig,
    ChainPipeline,
    build_audio_filter,
    build_chunk_plan,
    build_renderer_argv,
    choose_best_attempt,
    evaluate_gate,
    extract_frame,
    extract_sample_frames,
    load_state,
    main,
    probe_video,
    save_state,
    seed_for,
)


def _config(tmp_path: Path, *, total: int = 111, retries: int = 1) -> ChainConfig:
    prompt = tmp_path / "prompt.txt"
    audio = tmp_path / "audio.m4a"
    image = tmp_path / "portrait.png"
    prompt.write_text("character description stays verbatim", encoding="utf-8")
    audio.write_bytes(b"fake-audio")
    image.write_bytes(b"fake-image")
    return ChainConfig(
        image=str(image),
        prompt_file=prompt,
        audio=audio,
        output=tmp_path / "out.mp4",
        chunk_frames=56,
        total_frames=total,
        width=736,
        height=1120,
        steps=20,
        seed_base=42,
        max_retries=retries,
        max_face_drift=8.0,
        min_texture=0.0,
        max_texture_drop=0.25,
        state=tmp_path / "state.json",
        server="",
    )


def test_chunk_plan_preserves_exact_output_and_renderer_lengths() -> None:
    plan = build_chunk_plan(total_frames=120, chunk_frames=56, seed_base=42)

    assert [c.render_frames for c in plan] == [56, 56, 22]
    assert [c.kept_frames for c in plan] == [56, 55, 9]
    assert [c.output_start for c in plan] == [0, 56, 111]
    assert [c.seed for c in plan] == [42, 1_000_042, 2_000_042]
    assert sum(c.kept_frames for c in plan) == 120
    assert all((c.render_frames - 5) % 17 == 0 for c in plan)
    assert sum(c.render_frames for c in plan) - (len(plan) - 1) - 120 == 12


def test_chunk_plan_has_no_tail_trim_when_lengths_align() -> None:
    plan = build_chunk_plan(total_frames=111, chunk_frames=56, seed_base=0)
    assert [c.render_frames for c in plan] == [56, 56]
    assert [c.kept_frames for c in plan] == [56, 55]
    assert sum(c.kept_frames for c in plan) == 111


@pytest.mark.parametrize("chunk_frames", [0, 4, 6, 55])
def test_chunk_plan_rejects_lengths_h3_cannot_render(chunk_frames: int) -> None:
    with pytest.raises(ValueError, match="17"):
        build_chunk_plan(total_frames=111, chunk_frames=chunk_frames, seed_base=0)


def test_seed_changes_for_chunk_and_retry() -> None:
    assert seed_for(10, 0, 0) == 10
    assert seed_for(10, 1, 0) == 1_000_010
    assert seed_for(10, 1, 2) == 1_000_012


def test_plan_cli_is_offline_and_writes_no_state(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.delenv("COMFYUI_PROD_DIR", raising=False)

    def forbidden(*args: object, **kwargs: object) -> None:
        raise AssertionError("plan must not start subprocesses")

    monkeypatch.setattr(subprocess, "run", forbidden)
    state = tmp_path / "must-not-exist.json"
    rc = main(
        [
            "--image", "portrait.png",
            "--prompt-file", "prompt.txt",
            "--audio", "audio.m4a",
            "--output", str(tmp_path / "out.mp4"),
            "--chunk-frames", "56",
            "--total-frames", "120",
            "--seed-base", "42",
            "--state", str(state),
            "--plan",
        ]
    )

    assert rc == 0
    printed = capsys.readouterr().out
    assert "chunk" in printed and "render" in printed and "kept" in printed
    assert "56" in printed and "22" in printed and "120" in printed
    assert not state.exists()


def test_gate_checks_both_face_drift_and_texture() -> None:
    metrics = {"face_drift": 4.0, "pore": 100.0, "frames": 12}
    good = evaluate_gate(metrics, max_face_drift=8.0, texture_floor=80.0)
    assert good.accepted and good.reasons == []

    drifting = evaluate_gate(
        {**metrics, "face_drift": 9.0}, max_face_drift=8.0, texture_floor=80.0
    )
    assert not drifting.accepted and "face_drift" in drifting.reasons[0]

    smooth = evaluate_gate(
        {**metrics, "pore": 70.0}, max_face_drift=8.0, texture_floor=80.0
    )
    assert not smooth.accepted and "texture" in smooth.reasons[0]


def test_gate_rejects_missing_or_nonfinite_metrics() -> None:
    result = evaluate_gate(
        {"face_drift": float("nan"), "pore": 1.0, "frames": 12},
        max_face_drift=8.0,
        texture_floor=0.0,
    )
    assert not result.accepted
    assert "face_drift" in result.reasons[0]


def test_best_failed_attempt_uses_normalized_gate_penalty() -> None:
    attempts = [
        {"attempt": 0, "metrics": {"face_drift": 16.0, "pore": 100.0}},
        {"attempt": 1, "metrics": {"face_drift": 12.0, "pore": 100.0}},
        {"attempt": 2, "metrics": {"face_drift": 11.0, "pore": 10.0}},
    ]
    best = choose_best_attempt(attempts, max_face_drift=8.0, texture_floor=80.0)
    assert best["attempt"] == 1


def test_renderer_argv_uses_input_basename_audio_offset_and_seed(tmp_path: Path) -> None:
    argv = build_renderer_argv(
        image_name="anchor.png",
        output=tmp_path / "chunk.mp4",
        prompt_file=tmp_path / "prompt.txt",
        audio=tmp_path / "audio.m4a",
        prefix="job-c0-a0",
        width=736,
        height=1120,
        render_frames=56,
        steps=20,
        seed=123,
        audio_offset_frames=56,
        server="http://127.0.0.1:8188",
    )
    assert argv[argv.index("--image") + 1] == "anchor.png"
    assert argv[argv.index("--audio") + 1].endswith("audio.m4a")
    assert argv[argv.index("--audio-offset") + 1] == "56"
    assert argv[argv.index("--seed") + 1] == "123"
    assert "--guide-image" not in argv


def test_audio_filter_crossfades_only_the_kept_durations() -> None:
    graph = build_audio_filter([56, 55, 9], fps=24.0)
    assert graph.count("acrossfade=d=0.04:c1=qsin:c2=qsin") == 2
    assert "atrim=end=2.333333" in graph
    assert "atrim=end=2.291667" in graph
    assert "atrim=end=0.375" in graph


def test_state_round_trip_is_atomic_and_validated(tmp_path: Path) -> None:
    path = tmp_path / "state.json"
    state = {"schema_version": 1, "fingerprint": "abc", "chunks": []}
    save_state(path, state)
    assert load_state(path, expected_fingerprint="abc") == state
    assert not path.with_name(path.name + ".tmp").exists()
    with pytest.raises(ValueError, match="fingerprint"):
        load_state(path, expected_fingerprint="different")


class FakePipeline(ChainPipeline):
    def __init__(self, config: ChainConfig, measurements: dict[int, list[dict[str, float]]]):
        super().__init__(config)
        self.measurements = measurements
        self.render_calls: list[dict[str, object]] = []
        self.assemble_calls = 0

    def _prepare_initial_image(self) -> str:
        self._initial_source = Path(self.config.image)
        self.job_id = "fake-job"
        return "initial.png"

    def _stage_anchor(self, local_path: Path, chunk_index: int) -> str:
        return f"anchor-{chunk_index}.png"

    def _render_attempt(self, **kwargs: object) -> Path:
        self.render_calls.append(kwargs)
        output = kwargs["output"]
        assert isinstance(output, Path)
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_bytes(b"fake-video")
        return output

    def _measure_chunk(self, video: Path, attempt_dir: Path) -> dict[str, float]:
        index = int(video.name.split("_")[1])
        attempt = int(video.name.split("_")[2].split(".")[0])
        return self.measurements[index][attempt]

    def _extract_last_frame(self, video: Path, output: Path) -> Path:
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_bytes(b"fake-anchor")
        return output

    def _assemble(self, plan: object) -> None:
        self.assemble_calls += 1
        self.config.output.write_bytes(b"fake-output")

    def _measure_seams(self, plan: object) -> list[dict[str, object]]:
        return []


def test_retry_rerenders_only_bad_chunk_with_new_seed(tmp_path: Path) -> None:
    config = _config(tmp_path)
    pipeline = FakePipeline(
        config,
        {
            0: [{"face_drift": 1.0, "pore": 100.0, "frames": 56}],
            1: [
                {"face_drift": 20.0, "pore": 100.0, "frames": 56},
                {"face_drift": 2.0, "pore": 110.0, "frames": 56},
            ],
        },
    )

    report = pipeline.run()

    assert [call["chunk_index"] for call in pipeline.render_calls] == [0, 1, 1]
    first_bad = pipeline.render_calls[1]
    retry = pipeline.render_calls[2]
    assert first_bad["seed"] != retry["seed"]
    assert first_bad["image_name"] == retry["image_name"]
    assert first_bad["prompt_file"] == retry["prompt_file"] == config.prompt_file
    state = json.loads(config.state.read_text(encoding="utf-8"))
    assert len(state["chunks"][0]["attempts"]) == 1
    assert len(state["chunks"][1]["attempts"]) == 2
    assert report["chunks"][0]["gate_passed"] is True
    assert report["chunks"][1]["gate_passed"] is True
    assert report["summary"]["render_calls"] == 3


def test_resume_skips_accepted_chunks(tmp_path: Path) -> None:
    config = _config(tmp_path)
    measurements = {
        0: [{"face_drift": 1.0, "pore": 100.0, "frames": 56}],
        1: [{"face_drift": 2.0, "pore": 110.0, "frames": 56}],
    }
    first = FakePipeline(config, measurements)
    first.run()
    calls_after_first = len(first.render_calls)

    resumed = FakePipeline(config, measurements)
    resumed.run()

    assert calls_after_first == 2
    assert resumed.render_calls == []
    assert resumed.assemble_calls == 1


def test_resume_repairs_missing_anchor_without_rerender(tmp_path: Path) -> None:
    config = _config(tmp_path)
    measurements = {
        0: [{"face_drift": 1.0, "pore": 100.0, "frames": 56}],
        1: [{"face_drift": 2.0, "pore": 110.0, "frames": 56}],
    }
    first = FakePipeline(config, measurements)
    first.run()
    state = json.loads(config.state.read_text(encoding="utf-8"))
    Path(state["chunks"][0]["last_frame"]).unlink()

    resumed = FakePipeline(config, measurements)
    resumed.run()

    assert resumed.render_calls == []
    repaired = json.loads(config.state.read_text(encoding="utf-8"))
    assert Path(repaired["chunks"][0]["last_frame"]).exists()


def test_exhausted_gate_selects_best_but_reports_failure(tmp_path: Path) -> None:
    config = _config(tmp_path, total=56, retries=1)
    pipeline = FakePipeline(
        config,
        {
            0: [
                {"face_drift": 20.0, "pore": 100.0, "frames": 56},
                {"face_drift": 12.0, "pore": 90.0, "frames": 56},
            ]
        },
    )

    report = pipeline.run()

    assert report["chunks"][0]["selected_attempt"] == 1
    assert report["chunks"][0]["gate_passed"] is False
    assert report["summary"]["quality_gate_passed"] is False
    assert config.output.exists()


def test_real_ffmpeg_assembly_dedups_and_preserves_exact_frame_count(tmp_path: Path) -> None:
    if not shutil.which("ffmpeg") or not shutil.which("ffprobe"):
        pytest.skip("ffmpeg and ffprobe are required")
    config = replace(_config(tmp_path, total=9, retries=0), chunk_frames=5)
    videos = []
    for index in range(2):
        video = tmp_path / f"input_{index}.mp4"
        subprocess.run(
            [
                "ffmpeg", "-y", "-v", "error",
                "-f", "lavfi", "-i", "testsrc=size=64x64:rate=24",
                "-f", "lavfi", "-i", f"sine=frequency={440 + index * 110}:duration=1",
                "-frames:v", "5", "-c:v", "libx264", "-pix_fmt", "yuv420p",
                "-c:a", "aac", "-shortest", str(video),
            ],
            check=True,
            timeout=120,
        )
        videos.append(video)

    pipeline = ChainPipeline(config)
    pipeline.state = {"chunks": [{"video": str(video)} for video in videos]}
    pipeline._assemble(pipeline.plan)

    assert probe_video(config.output)["frames"] == 9
    samples = extract_sample_frames(videos[0], tmp_path / "samples", samples=4)
    assert len(samples) == 4
    actual_last = pipeline._extract_last_frame(videos[0], tmp_path / "actual_last.png")
    expected_last = extract_frame(videos[0], tmp_path / "expected_last.png", 4)
    assert actual_last.read_bytes() == expected_last.read_bytes()
