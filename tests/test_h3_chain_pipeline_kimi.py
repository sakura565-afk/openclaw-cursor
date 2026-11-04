"""Offline tests for the resumable H3 last-frame chain pipeline."""

from __future__ import annotations

import json
import math
import shutil
import subprocess
import wave
from dataclasses import replace
from pathlib import Path

import numpy as np
import pytest

from scripts.h3_chain_pipeline_kimi.pipeline import (
    ChainConfig,
    ChainPipeline,
    SCHEMA_VERSION,
    build_audio_filter,
    build_chunk_plan,
    build_renderer_argv,
    choose_best_attempt,
    evaluate_gate,
    extract_frame,
    extract_sample_frames,
    extract_tail_frames,
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
        identity_lora="none",
        tail_frames=5,
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


def test_gate_checks_mean_and_tail_face_drift_and_texture() -> None:
    metrics = {
        "face_drift": 4.0,
        "face_drift_tail": 5.0,
        "tail_frames": 5,
        "pore": 100.0,
        "frames": 12,
    }
    good = evaluate_gate(metrics, max_face_drift=8.0, texture_floor=80.0)
    assert good.accepted and good.reasons == []

    drifting = evaluate_gate(
        {**metrics, "face_drift": 9.0}, max_face_drift=8.0, texture_floor=80.0
    )
    assert not drifting.accepted and "face_drift" in drifting.reasons[0]

    # This is the failure mode a whole-clip mean hides: the head is stable at
    # first, then walks away in the final frames of the chunk.
    drifting_tail = evaluate_gate(
        {**metrics, "face_drift_tail": 12.0},
        max_face_drift=8.0,
        texture_floor=80.0,
    )
    assert not drifting_tail.accepted
    assert any("tail" in reason for reason in drifting_tail.reasons)

    smooth = evaluate_gate(
        {**metrics, "pore": 70.0}, max_face_drift=8.0, texture_floor=80.0
    )
    assert not smooth.accepted and "texture" in smooth.reasons[0]


@pytest.mark.parametrize("pore", [0.0, -1.0, float("nan"), None])
def test_gate_fails_closed_for_zero_or_unmeasurable_texture(pore: object) -> None:
    result = evaluate_gate(
        {
            "face_drift": 1.0,
            "face_drift_tail": 1.0,
            "tail_frames": 5,
            "pore": pore,
            "frames": 12,
        },
        max_face_drift=8.0,
        texture_floor=0.0,
    )
    assert not result.accepted
    assert any("texture" in reason for reason in result.reasons)


def test_gate_rejects_missing_or_nonfinite_metrics() -> None:
    result = evaluate_gate(
        {"face_drift": float("nan"), "pore": 1.0, "frames": 12},
        max_face_drift=8.0,
        texture_floor=0.0,
    )
    assert not result.accepted
    assert "face_drift" in result.reasons[0]
    assert any("tail" in reason for reason in result.reasons)


def test_best_failed_attempt_uses_normalized_gate_penalty() -> None:
    attempts = [
        {"attempt": 0, "metrics": {"face_drift": 16.0, "face_drift_tail": 16.0, "tail_frames": 5, "pore": 100.0}},
        {"attempt": 1, "metrics": {"face_drift": 12.0, "face_drift_tail": 12.0, "tail_frames": 5, "pore": 100.0}},
        {"attempt": 2, "metrics": {"face_drift": 11.0, "face_drift_tail": 11.0, "tail_frames": 5, "pore": 10.0}},
    ]
    best = choose_best_attempt(attempts, max_face_drift=8.0, texture_floor=80.0)
    assert best["attempt"] == 1


def test_renderer_argv_matches_real_help_and_has_no_audio_offset_or_server(tmp_path: Path) -> None:
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
    )
    assert argv[argv.index("--image") + 1] == "anchor.png"
    assert argv[argv.index("--audio") + 1].endswith("audio.m4a")
    assert argv[argv.index("--seed") + 1] == "123"
    assert "--audio-offset" not in argv
    assert "--server" not in argv
    assert "--guide-image" not in argv

    helped = subprocess.run(
        [argv[0], argv[2], "--help"], capture_output=True, text=True, timeout=60
    )
    assert helped.returncode == 0, helped.stderr
    unknown = [item for item in argv if item.startswith("--") and item not in helped.stdout]
    assert unknown == []


def test_renderer_argv_can_pin_character_swap_reference_and_lora(tmp_path: Path) -> None:
    argv = build_renderer_argv(
        image_name="anchor.png",
        identity_image_name="identity.png",
        identity_lora="character_swap",
        output=tmp_path / "chunk.mp4",
        prompt_file=tmp_path / "prompt.txt",
        audio=tmp_path / "audio.m4a",
        prefix="job-c0-a0",
        width=736,
        height=1120,
        render_frames=56,
        steps=20,
        seed=123,
    )
    assert argv[argv.index("--ref-image") + 1] == "identity.png"
    loras = json.loads(argv[argv.index("--loras-json") + 1])
    assert loras == [
        {
            "n": "minimax_h3_character_swap_v1.1_ref2va_fro.safetensors",
            "sm": 1.0,
            "sc": 1.0,
        }
    ]


def test_audio_filter_seeks_one_source_and_overlaps_equal_power_crossfades() -> None:
    graph = build_audio_filter([56, 55, 9], fps=24.0)
    assert graph.count("[0:a]atrim=start=") == 3
    assert "[1:a]" not in graph and "[2:a]" not in graph
    assert "atrim=start=0.000000:end=2.333333" in graph
    assert "atrim=start=2.293333:end=4.625000" in graph
    assert "atrim=start=4.585000:end=5.000000" in graph
    assert graph.count("acrossfade=d=0.04:c1=qsin:c2=qsin") == 2


def test_state_round_trip_is_atomic_and_validated(tmp_path: Path) -> None:
    path = tmp_path / "state.json"
    state = {"schema_version": SCHEMA_VERSION, "fingerprint": "abc", "chunks": []}
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
        measured = dict(self.measurements[index][attempt])
        measured.setdefault("face_drift_tail", measured["face_drift"])
        measured.setdefault("tail_frames", 5)
        return measured

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


def test_seed_base_change_invalidates_accepted_state(tmp_path: Path) -> None:
    config = _config(tmp_path)
    measurements = {
        0: [{"face_drift": 1.0, "pore": 100.0, "frames": 56}],
        1: [{"face_drift": 2.0, "pore": 110.0, "frames": 56}],
    }
    FakePipeline(config, measurements).run()

    changed = replace(config, seed_base=config.seed_base + 1)
    resumed = FakePipeline(changed, measurements)
    resumed.run()

    assert [call["seed"] for call in resumed.render_calls] == [43, 1_000_043]
    state = json.loads(changed.state.read_text(encoding="utf-8"))
    assert state["config"]["seed_base"] == 43
    assert [chunk["initial_seed"] for chunk in state["chunks"]] == [43, 1_000_043]


def test_measure_chunk_adds_contiguous_tail_drift(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    sample_paths = [tmp_path / f"sample_{index}.png" for index in range(4)]
    tail_paths = [tmp_path / f"tail_{index}.png" for index in range(2)]
    monkeypatch.setattr(
        "scripts.h3_chain_pipeline_kimi.pipeline.extract_sample_frames",
        lambda *args, **kwargs: sample_paths,
    )
    monkeypatch.setattr(
        "scripts.h3_chain_pipeline_kimi.pipeline.extract_tail_frames",
        lambda *args, **kwargs: tail_paths,
    )
    calls: list[list[str]] = []

    def fake_measure(paths: list[Path]) -> dict[str, float | int]:
        names = [path.name for path in paths]
        calls.append(names)
        if any(name.startswith("tail_") for name in names):
            return {"face_drift": 9.0, "face_drift_max": 12.0, "pore": 100.0, "frames": 3}
        return {"face_drift": 1.0, "face_drift_max": 2.0, "pore": 100.0, "frames": 4}

    monkeypatch.setattr("scripts.h3_chain_pipeline_kimi.pipeline._texture_measure", fake_measure)
    metrics = ChainPipeline(_config(tmp_path))._measure_chunk(tmp_path / "chunk.mp4", tmp_path)

    assert calls[1] == [sample_paths[0].name, *(path.name for path in tail_paths)]
    assert metrics["face_drift"] == 1.0
    assert metrics["face_drift_tail"] == 9.0
    assert metrics["face_drift_last"] == 12.0
    assert metrics["tail_frames"] == 2


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
    _write_click_track(config.audio, [0.1, 0.25], config.total_frames / 24.0)
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
    tail = extract_tail_frames(videos[0], tmp_path / "tail", frames=2)
    assert [path.read_bytes() for path in tail] == [
        extract_frame(videos[0], tmp_path / "tail_3.png", 3).read_bytes(),
        expected_last.read_bytes(),
    ]


def _write_click_track(path: Path, times: list[float], duration: float) -> None:
    rate = 48_000
    samples = np.zeros(round(duration * rate), dtype=np.float64)
    for time in times:
        start = round(time * rate)
        count = round(0.012 * rate)
        envelope = np.exp(-np.linspace(0.0, 5.0, count))
        tone = np.sin(2.0 * math.pi * 1800.0 * np.arange(count) / rate)
        samples[start : start + count] += 0.9 * envelope * tone
    with wave.open(str(path), "wb") as output:
        output.setnchannels(1)
        output.setsampwidth(2)
        output.setframerate(rate)
        output.writeframes((np.clip(samples, -1.0, 1.0) * 32767).astype("<i2").tobytes())


def _click_times(path: Path) -> list[float]:
    with wave.open(str(path), "rb") as source:
        rate = source.getframerate()
        samples = np.frombuffer(source.readframes(source.getnframes()), dtype="<i2")
    loud = np.flatnonzero(np.abs(samples) > 500)
    groups = np.split(loud, np.flatnonzero(np.diff(loud) > round(0.03 * rate)) + 1)
    return [float(group[0] / rate) for group in groups if len(group)]


class FakeMediaPipeline(FakePipeline):
    """Fake H3 with real MP4s whose audio wrongly restarts at zero per chunk."""

    def _render_attempt(self, **kwargs: object) -> Path:
        self.render_calls.append(kwargs)
        output = kwargs["output"]
        assert isinstance(output, Path)
        output.parent.mkdir(parents=True, exist_ok=True)
        subprocess.run(
            [
                "ffmpeg", "-y", "-v", "error",
                "-f", "lavfi", "-i", "testsrc=size=64x64:rate=24",
                "-i", str(self.config.audio),
                "-frames:v", str(kwargs["render_frames"]),
                "-c:v", "libx264", "-pix_fmt", "yuv420p",
                "-c:a", "aac", "-shortest", str(output),
            ],
            check=True,
            timeout=120,
        )
        return output

    def _extract_last_frame(self, video: Path, output: Path) -> Path:
        return ChainPipeline._extract_last_frame(self, video, output)

    def _assemble(self, plan: object) -> None:
        ChainPipeline._assemble(self, plan)


def test_fake_renderer_assembly_seeks_audio_and_keeps_click_track_timing(tmp_path: Path) -> None:
    if not shutil.which("ffmpeg") or not shutil.which("ffprobe"):
        pytest.skip("ffmpeg and ffprobe are required")
    config = replace(_config(tmp_path, total=64, retries=0), chunk_frames=22)
    click_times = [0.2, 0.6, 1.1, 1.5, 2.1, 2.5]
    _write_click_track(config.audio, click_times, config.total_frames / 24.0)
    measurements = {
        0: [{"face_drift": 1.0, "pore": 100.0, "frames": 22}],
        1: [{"face_drift": 1.0, "pore": 100.0, "frames": 22}],
        2: [{"face_drift": 1.0, "pore": 100.0, "frames": 22}],
    }

    report = FakeMediaPipeline(config, measurements).run()

    assert report["summary"]["quality_gate_passed"] is True
    assert probe_video(config.output)["frames"] == 64
    assembled_audio = config.work_dir / "audio.wav"
    actual = _click_times(assembled_audio)
    assert actual == pytest.approx(click_times, abs=0.003)
    # In particular, there is no second zero-based click track after the seam.
    assert len(actual) == len(click_times)
    for seam in (22 / 24.0, 43 / 24.0):
        assert not any(abs(value - seam) < 0.04 for value in actual)
