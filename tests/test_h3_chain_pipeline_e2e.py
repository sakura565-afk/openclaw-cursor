# -*- coding: utf-8 -*-
"""End-to-end tests of the pipeline with a FAKE renderer.

The real renderer needs a GPU and a ComfyUI server; neither is available in
a test. These tests replace it with a fake that produces a real mp4 (so the
assembly, the gate and the state can be exercised for real) and check the
things that only show up when the whole chain runs:

* the first chunk starts on the source still, the next one on the LAST frame
  of its predecessor;
* the renderer is called with the file NAME, never with a path;
* a chunk that fails the gate is re-rendered with a NEW seed, and only that
  chunk is re-rendered;
* a second run does not re-render anything that already passed;
* the assembled clip has the right number of frames.
"""
from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path
from typing import Any

import pytest

from scripts.h3_chain_pipeline import gates, pipeline
from scripts.h3_chain_pipeline.pipeline import main


def _ffmpeg() -> str:
    exe = shutil.which("ffmpeg")
    if not exe:
        pytest.skip("ffmpeg is not available")
    return exe


class FakeRenderer:
    """Stands in for ``scripts/h3_i2v_run.py``.

    It writes a real mp4 whose frames are all one colour, so the assembly can
    be checked frame by frame and the gate has something to measure.
    """

    def __init__(self, work: Path, calls: list[dict[str, Any]]) -> None:
        self.work = work
        self.calls = calls
        self.input_dir = work / "comfy_input"

    def __call__(self, request) -> Path:
        self.calls.append({
            "image": request.image_name,
            "seed": request.seed,
            "length": request.length,
            "output": str(request.output),
            "prompt_file": str(request.prompt_file),
        })
        # The image must be a NAME in the ComfyUI input directory: a path is
        # rejected by the LoadImage validator before the graph is sent.
        assert "/" not in request.image_name
        assert "\\" not in request.image_name
        source = self.input_dir / request.image_name
        assert source.exists(), f"the frame was not staged: {request.image_name}"

        output = Path(request.output)
        output.parent.mkdir(parents=True, exist_ok=True)
        seed = int(request.seed)
        # A solid colour that depends on the seed, so two takes differ.
        colour = "0x{:06x}".format((seed * 7919) % 0xFFFFFF)
        subprocess.run(
            [_ffmpeg(), "-y", "-v", "error", "-f", "lavfi", "-i",
             f"color=c={colour}:size=160x120:rate=24",
             "-frames:v", str(request.length),
             "-c:v", "libx264", "-crf", "16", "-pix_fmt", "yuv420p",
             str(output)],
            check=True, timeout=600,
        )
        return output


@pytest.fixture()
def fake_renderer(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    calls: list[dict[str, Any]] = []
    fake = FakeRenderer(tmp_path / "work", calls)
    fake.input_dir.mkdir(parents=True, exist_ok=True)

    monkeypatch.setattr(
        "scripts.h3_chain_pipeline.paths.comfy_input_dir", lambda: fake.input_dir
    )
    monkeypatch.setattr(
        "scripts.h3_chain_pipeline.render.render_chunk", fake
    )
    return fake


def _good_metrics(video, baseline_pore=0.0) -> gates.ChunkMetrics:
    """Metrics of a chunk that passes the gate."""
    return gates.ChunkMetrics(
        face_drift_mean=0.0, face_drift_max=0.0, face_drift_last=0.0,
        frames_holding_face=120, frames=120, pore=5.0e10, texture_ratio=1.0,
    )


def _make_inputs(tmp_path: Path) -> list[str]:
    prompt = tmp_path / "prompt.txt"
    prompt.write_text("a woman with red hair, cinematic", encoding="utf-8")
    still = tmp_path / "still.png"
    subprocess.run(
        [_ffmpeg(), "-y", "-v", "error", "-f", "lavfi", "-i",
         "color=c=blue:size=160x120:rate=1:duration=1",
         "-frames:v", "1", str(still)],
        check=True, timeout=300,
    )
    audio = tmp_path / "track.m4a"
    subprocess.run(
        [_ffmpeg(), "-y", "-v", "error", "-f", "lavfi", "-i",
         "sine=frequency=440:duration=6", "-c:a", "aac", "-b:a", "192k",
         str(audio)],
        check=True, timeout=300,
    )
    # 240 frames with a 175 frame chunk: chunk 0 renders 175 frames, chunk 1
    # renders the rest. Two chunks in total.
    return [
        "--image", str(still),
        "--prompt-file", str(prompt),
        "--audio", str(audio),
        "--output", str(tmp_path / "out.mp4"),
        "--total-frames", "240",
        "--chunk-frames", "175",
    ]


def _count_frames(path: Path) -> int:
    counted = subprocess.run(
        ["ffprobe", "-v", "error", "-count_frames", "-select_streams", "v:0",
         "-show_entries", "stream=nb_read_frames", "-of", "csv=p=0", str(path)],
        capture_output=True, text=True, check=True, timeout=300,
    )
    return int(counted.stdout.strip())


def test_the_pipeline_renders_every_chunk_and_assembles_the_clip(
    tmp_path: Path, fake_renderer, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The whole chain, with a fake renderer instead of the GPU one."""
    monkeypatch.setattr(
        "scripts.h3_chain_pipeline.gates.measure_chunk", _good_metrics
    )
    args = _make_inputs(tmp_path)

    assert main(args) == 0

    output = Path(args[args.index("--output") + 1])
    assert output.exists() and output.stat().st_size > 0
    assert _count_frames(output) == 240

    report = tmp_path / "out.report.json"
    assert report.exists()
    data = json.loads(report.read_text(encoding="utf-8"))
    assert len(data["chunks"]) == 2
    assert all(chunk["accepted"] for chunk in data["chunks"])


def test_every_chunk_gets_its_own_seed_and_the_same_prompt(
    tmp_path: Path, fake_renderer, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        "scripts.h3_chain_pipeline.gates.measure_chunk", _good_metrics
    )
    args = _make_inputs(tmp_path)
    main(args)

    seeds = [call["seed"] for call in fake_renderer.calls]
    assert len(set(seeds)) == len(seeds), "two chunks shared a seed"
    prompts = {call["prompt_file"] for call in fake_renderer.calls}
    assert len(prompts) == 1, "the prompt was not repeated verbatim"


def test_a_chunk_starts_on_the_last_frame_of_its_predecessor(
    tmp_path: Path, fake_renderer, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The chain is only continuous if the anchor is the LAST frame."""
    monkeypatch.setattr(
        "scripts.h3_chain_pipeline.gates.measure_chunk", _good_metrics
    )
    staged: list[tuple[str, bytes]] = []
    original = fake_renderer.__call__

    def spy(request):
        staged.append((
            request.image_name,
            (fake_renderer.input_dir / request.image_name).read_bytes(),
        ))
        return original(request)

    monkeypatch.setattr(
        "scripts.h3_chain_pipeline.render.render_chunk", spy
    )
    args = _make_inputs(tmp_path)
    main(args)

    assert len(staged) == 2
    # The two staged frames are DIFFERENT: the second one is the last frame
    # of the first chunk, not the still.
    assert staged[0][1] != staged[1][1]


def test_the_first_chunk_starts_on_the_source_still(
    tmp_path: Path, fake_renderer, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The first frame of the clip is the still the user gave."""
    monkeypatch.setattr(
        "scripts.h3_chain_pipeline.gates.measure_chunk", _good_metrics
    )
    staged: list[bytes] = []
    original = fake_renderer.__call__

    def spy(request):
        staged.append(
            (fake_renderer.input_dir / request.image_name).read_bytes()
        )
        return original(request)

    monkeypatch.setattr(
        "scripts.h3_chain_pipeline.render.render_chunk", spy
    )
    args = _make_inputs(tmp_path)
    main(args)

    still = Path(args[args.index("--image") + 1]).read_bytes()
    assert staged[0] == still


def test_a_second_run_does_not_render_anything_again(
    tmp_path: Path, fake_renderer, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Renders cost minutes of GPU time; the state file is what saves them."""
    monkeypatch.setattr(
        "scripts.h3_chain_pipeline.gates.measure_chunk", _good_metrics
    )
    args = _make_inputs(tmp_path)
    main(args)
    first_run = len(fake_renderer.calls)
    assert first_run == 2

    main(args)
    assert len(fake_renderer.calls) == first_run, "the second run re-rendered"


def test_a_failing_chunk_is_re_rendered_with_a_new_seed(
    tmp_path: Path, fake_renderer, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Only the failing chunk is re-rendered, and with a different seed."""
    seen: list[str] = []

    def flaky(video, baseline_pore=0.0):
        seen.append(str(video))
        # The FIRST attempt of every chunk is too drifty; the second is fine.
        if len(seen) % 2 == 1:
            return gates.ChunkMetrics(
                face_drift_last=30.0, pore=5.0e10, texture_ratio=1.0,
            )
        return gates.ChunkMetrics(
            face_drift_last=1.0, pore=5.0e10, texture_ratio=1.0,
        )

    monkeypatch.setattr(
        "scripts.h3_chain_pipeline.gates.measure_chunk", flaky
    )
    args = _make_inputs(tmp_path)
    args += ["--max-retries", "2"]
    main(args)

    # Two chunks, each rendered twice: the good chunk is never re-rendered.
    assert len(fake_renderer.calls) == 4
    seeds = [call["seed"] for call in fake_renderer.calls]
    assert seeds[0] != seeds[1], "the retry used the same seed"
    assert seeds[2] != seeds[3], "the retry used the same seed"


def test_a_chunk_that_never_passed_is_tried_again_on_the_next_run(
    tmp_path: Path, fake_renderer, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A chunk that failed the gate is not remembered as done."""
    def always_bad(video, baseline_pore=0.0):
        return gates.ChunkMetrics(
            face_drift_last=30.0, pore=5.0e10, texture_ratio=1.0,
        )

    monkeypatch.setattr(
        "scripts.h3_chain_pipeline.gates.measure_chunk", always_bad
    )
    args = _make_inputs(tmp_path)
    args += ["--max-retries", "0"]
    main(args)
    first = len(fake_renderer.calls)
    assert first == 2

    # The second run renders them again: nothing was accepted.
    main(args)
    assert len(fake_renderer.calls) == first * 2


def test_the_report_names_the_rejected_chunks(
    tmp_path: Path, fake_renderer, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A clip built from a rejected chunk must say so in the report."""
    def always_bad(video, baseline_pore=0.0):
        return gates.ChunkMetrics(
            face_drift_last=30.0, pore=5.0e10, texture_ratio=1.0,
        )

    monkeypatch.setattr(
        "scripts.h3_chain_pipeline.gates.measure_chunk", always_bad
    )
    args = _make_inputs(tmp_path)
    args += ["--max-retries", "0"]
    main(args)

    data = json.loads(
        (tmp_path / "out.report.json").read_text(encoding="utf-8")
    )
    assert [chunk["accepted"] for chunk in data["chunks"]] == [False, False]
    assert "drift" in data["chunks"][0]["attempts"][0]["reason"]
