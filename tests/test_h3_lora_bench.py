from __future__ import annotations

import json
import subprocess
from pathlib import Path

import numpy as np
import pytest

from scripts import h3_lora_bench as bench


def _frames() -> list[np.ndarray]:
    flat = np.full((32, 48, 3), 80, dtype=np.uint8)
    textured = flat.copy()
    textured[8:24, 10:38] = np.indices((16, 28)).sum(axis=0)[..., None] % 2 * 180
    shifted = np.roll(textured, 1, axis=1)
    return [flat, textured, shifted]


def test_measure_frames_reports_texture_temporal_face_and_parked_pairs() -> None:
    frames = _frames()
    result = bench.measure_frames(
        frames + [frames[-1]],
        reference=frames[0],
        face_roi=(0.0, 0.0, 1.0, 1.0),
        skin_mask=np.ones(frames[0].shape[:2], dtype=np.float32),
    )

    assert result["frame_count"] == 4
    assert result["microtexture_whole"] > 0
    assert result["microtexture_skin"] > 0
    assert result["temporal_rgb_delta"] > 0
    assert result["temporal_hf_delta"] > 0
    assert result["face_mae"] > 0
    assert result["parked_frame_pairs"] == 1
    assert result["parked_max_run"] == 2


def test_compare_against_baseline_requires_texture_without_flicker_regression() -> None:
    baseline = {
        "microtexture_whole": 10.0,
        "microtexture_skin": 12.0,
        "temporal_hf_delta": 2.0,
        "face_mae": 8.0,
        "parked_frame_pairs": 0,
    }
    candidate = dict(baseline, microtexture_skin=13.0)
    rejected = dict(candidate, temporal_hf_delta=3.0)

    assert bench.compare_to_baseline(candidate, baseline)["verdict"] == "candidate"
    assert bench.compare_to_baseline(rejected, baseline)["verdict"] == "reject"


def test_zero_baseline_hf_with_candidate_flicker_is_rejected() -> None:
    baseline = {"microtexture_whole": 0.0, "microtexture_skin": 0.0, "temporal_hf_delta": 0.0, "face_mae": 0.0, "parked_frame_pairs": 0}
    candidate = {"microtexture_whole": 1.0, "microtexture_skin": 1.0, "temporal_hf_delta": 0.1, "face_mae": 0.0, "parked_frame_pairs": 0}
    assert bench.compare_to_baseline(candidate, baseline)["verdict"] == "reject"


def test_run_bench_is_sequential_and_reuses_one_same_seed_baseline(tmp_path: Path) -> None:
    reference = tmp_path / "reference.png"
    reference.write_bytes(b"reference")
    prompt = tmp_path / "prompt.txt"
    prompt.write_text("test trigger", encoding="utf-8")
    audio = tmp_path / "audio.wav"
    audio.write_bytes(b"audio")
    calls: list[dict] = []

    def fake_render(spec, timeout_s):
        calls.append(spec)
        spec.output.write_bytes(spec.lora_name.encode())

    def fake_measure(path, **kwargs):
        name = path.read_bytes().decode()
        return {
            "microtexture_whole": 10.0,
            "microtexture_skin": 13.0 if name else 12.0,
            "temporal_hf_delta": 2.0,
            "face_mae": 8.0,
            "parked_frame_pairs": 0,
        }

    config = bench.BenchConfig(
        reference=reference,
        prompt_file=prompt,
        audio=audio,
        output_dir=tmp_path / "out",
        loras=[bench.LoraSpec("one.safetensors", 0.5), bench.LoraSpec("two.safetensors", 0.75)],
        seed=123,
        runner=Path("runner.py"),
    )
    result = bench.run_bench(config, render_fn=fake_render, measure_fn=fake_measure, wait_fn=lambda **_: None)

    assert len(calls) == 3
    assert calls[0].lora_name == ""
    assert {call.seed for call in calls} == {123}
    assert len(result["renders"]) == 3
    assert result["renders"][1]["comparison"]["verdict"] == "candidate"
    assert (tmp_path / "out" / "summary.json").exists()
    assert len((tmp_path / "out" / "results.jsonl").read_text(encoding="utf-8").splitlines()) == 3


def test_render_command_has_timeout_lora_and_fixed_recipe(tmp_path: Path) -> None:
    spec = bench.RenderSpec(
        lora_name="detail.safetensors",
        strength=0.6,
        reference="reference.png",
        prompt_file=tmp_path / "prompt.txt",
        audio=tmp_path / "audio.wav",
        output=tmp_path / "out.mp4",
        prefix="h3_lora_test",
        runner=tmp_path / "runner.py",
        width=640,
        height=384,
        length=73,
        steps=16,
        seed=99,
        wait=840,
    )

    command = bench.render_command(spec)

    assert command[1].endswith("runner.py")
    assert command[command.index("--seed") + 1] == "99"
    assert command[command.index("--length") + 1] == "73"
    assert json.loads(command[command.index("--loras-json") + 1]) == [
        {"n": "detail.safetensors", "sm": 0.6, "sc": 0.6}
    ]


def test_subprocess_render_enforces_wall_clock_timeout(tmp_path: Path, monkeypatch) -> None:
    spec = bench.RenderSpec(
        lora_name="", strength=0.0, reference="ref.png", prompt_file=tmp_path / "p.txt",
        audio=tmp_path / "a.wav", output=tmp_path / "o.mp4", prefix="x", runner=tmp_path / "r.py",
        width=640, height=384, length=73, steps=16, seed=1, wait=10,
    )

    def timeout(*args, **kwargs):
        raise subprocess.TimeoutExpired(args[0], kwargs["timeout"])

    monkeypatch.setattr(subprocess, "run", timeout)
    with pytest.raises(TimeoutError, match="15-minute|900"):
        bench.subprocess_render(spec, timeout_s=900)
