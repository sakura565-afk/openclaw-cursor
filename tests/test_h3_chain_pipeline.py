# -*- coding: utf-8 -*-
"""Tests for the H3 chain pipeline.

Three groups, one per thing that can silently go wrong:

* the ARITHMETIC of the chain - how many frames a chunk contributes, where
  the seams are, which seed a retry uses. This is pure logic and is tested
  hard, because a mistake here is invisible in the output and ruins the
  whole clip;
* the STATE - that a re-run does not re-render what was already accepted,
  and that a changed predecessor invalidates the chunks after it;
* the MECHANICS of the assembly - that the duplicated boundary frame is
  dropped and that the audio is welded.

Nothing here talks to ComfyUI or to a GPU.
"""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

from scripts.h3_chain_pipeline import assemble, gates, paths, plan, state
from scripts.h3_chain_pipeline.pipeline import build_parser, main, run


# --------------------------------------------------------------------- plan
def test_h3_accepts_only_lengths_of_17k_plus_5() -> None:
    """The sampler rejects other lengths, so the plan has to snap."""
    assert plan.h3_valid_length(56)      # 17 * 3 + 5
    assert plan.h3_valid_length(175)     # 17 * 10 + 5 - the default
    assert plan.h3_valid_length(226)     # 17 * 13 + 5
    assert not plan.h3_valid_length(174)
    assert not plan.h3_valid_length(100)


def test_next_valid_length_rounds_up_to_the_grid() -> None:
    assert plan.next_valid_length(56) == 56
    assert plan.next_valid_length(57) == 73
    assert plan.next_valid_length(1) == plan.MIN_H3_LENGTH


def test_a_chunk_size_off_the_grid_is_snapped_and_reported() -> None:
    result = plan.plan_chunks(total_frames=400, chunk_frames=100)
    assert result.chunk_frames_effective == 90  # 17 * 5 + 5
    assert any("snapped" in note for note in result.notes)


def test_the_plan_covers_the_whole_clip() -> None:
    """Every frame of the finished clip is produced by exactly one chunk."""
    result = plan.plan_chunks(total_frames=1000, chunk_frames=175)
    assert result.chunks
    assert result.chunks[0].out_first == 0
    for previous, following in zip(result.chunks, result.chunks[1:]):
        # The next chunk starts on the last frame of its predecessor, so it
        # contributes one frame less than it renders.
        assert following.out_first == previous.out_last + 1
        assert following.render_frames == following.out_frames + 1
    assert result.assembled_frames >= result.total_frames


def test_only_the_last_chunk_is_shortened() -> None:
    """The opening of the clip must not be sacrificed to the tail."""
    result = plan.plan_chunks(total_frames=1000, chunk_frames=175)
    for chunk in result.chunks[:-1]:
        assert chunk.render_frames == result.chunk_frames_effective
    assert result.chunks[-1].render_frames <= result.chunk_frames_effective


def test_every_chunk_gets_its_own_seed() -> None:
    result = plan.plan_chunks(total_frames=1000, chunk_frames=175, seed_base=42)
    seeds = [chunk.seed for chunk in result.chunks]
    assert len(set(seeds)) == len(seeds)
    assert seeds[0] == 42


def test_a_retry_uses_a_different_seed() -> None:
    result = plan.plan_chunks(total_frames=400, chunk_frames=175, seed_base=42)
    first = result.seed_for(0, 0)
    retry = result.seed_for(0, 1)
    assert first != retry
    # And the retry is a pure function of its inputs, so a re-run reproduces it.
    assert result.seed_for(0, 1) == retry


def test_the_seams_are_where_the_chunks_meet() -> None:
    result = plan.plan_chunks(total_frames=1000, chunk_frames=175, fps=24)
    boundaries = result.boundaries_seconds()
    assert len(boundaries) == len(result.chunks) - 1
    first = result.chunks[0]
    assert boundaries[0] == pytest.approx((first.out_last + 1) / 24.0)


def test_a_single_chunk_plan_has_no_seams() -> None:
    result = plan.plan_chunks(total_frames=100, chunk_frames=175)
    assert len(result.chunks) == 1
    assert result.boundaries_seconds() == []


def test_the_prompt_hash_changes_with_the_prompt(tmp_path: Path) -> None:
    first = tmp_path / "a.txt"
    second = tmp_path / "b.txt"
    first.write_text("a woman with red hair", encoding="utf-8")
    second.write_text("a woman with red hair", encoding="utf-8")
    assert plan.prompt_hash(first) == plan.prompt_hash(second)
    second.write_text("a woman with black hair", encoding="utf-8")
    assert plan.prompt_hash(first) != plan.prompt_hash(second)


# -------------------------------------------------------------------- state
def test_the_chunk_key_is_index_seed_and_prompt_hash() -> None:
    assert state.chunk_key(3, 77, "abcd") == "3:77:abcd"
    assert state.chunk_key(3, 78, "abcd") != state.chunk_key(3, 77, "abcd")
    assert state.chunk_key(4, 77, "abcd") != state.chunk_key(3, 77, "abcd")
    assert state.chunk_key(3, 77, "ef01") != state.chunk_key(3, 77, "abcd")


def test_an_accepted_chunk_is_reused_on_the_next_run(tmp_path: Path) -> None:
    video = tmp_path / "chunk.mp4"
    video.write_bytes(b"not really a video")
    pipeline_state = state.State(tmp_path / "state.json")
    pipeline_state.put(state.ChunkRecord(
        index=0, seed=42, prompt_hash="hash", accepted=True,
        video=str(video), last_frame=str(video),
    ))

    found = pipeline_state.accepted_for(0, "hash")
    assert found is not None
    assert found.seed == 42


def test_a_chunk_is_not_reused_when_its_file_is_gone(tmp_path: Path) -> None:
    pipeline_state = state.State(tmp_path / "state.json")
    pipeline_state.put(state.ChunkRecord(
        index=0, seed=42, prompt_hash="hash", accepted=True,
        video=str(tmp_path / "missing.mp4"),
    ))
    assert pipeline_state.accepted_for(0, "hash") is None


def test_a_changed_prompt_invalidates_the_chunk(tmp_path: Path) -> None:
    video = tmp_path / "chunk.mp4"
    video.write_bytes(b"x")
    pipeline_state = state.State(tmp_path / "state.json")
    pipeline_state.put(state.ChunkRecord(
        index=0, seed=42, prompt_hash="old", accepted=True, video=str(video),
    ))
    assert pipeline_state.accepted_for(0, "new") is None


def test_a_changed_anchor_invalidates_the_chunk(tmp_path: Path) -> None:
    """A chunk rendered from another frame belongs to another take."""
    video = tmp_path / "chunk.mp4"
    video.write_bytes(b"x")
    pipeline_state = state.State(tmp_path / "state.json")
    pipeline_state.put(state.ChunkRecord(
        index=1, seed=42, prompt_hash="hash", accepted=True,
        video=str(video), anchor="frame_a.png",
    ))
    assert pipeline_state.accepted_for(1, "hash", anchor="frame_a.png") is not None
    assert pipeline_state.accepted_for(1, "hash", anchor="frame_b.png") is None


def test_a_changed_seed_base_invalidates_the_cached_take(tmp_path: Path) -> None:
    """A chunk rendered with another seed belongs to another job."""
    video = tmp_path / "chunk.mp4"
    video.write_bytes(b"x")
    pipeline_state = state.State(tmp_path / "state.json")
    pipeline_state.put(state.ChunkRecord(
        index=0, seed=42, prompt_hash="hash", accepted=True,
        video=str(video), last_frame=str(video), anchor="",
    ))

    # The seed this run would have chosen is 42: the take is reused.
    assert pipeline_state.accepted_for(0, "hash", seeds={42}) is not None
    # Another seed base: the old take must not go into the clip.
    assert pipeline_state.accepted_for(0, "hash", seeds={4242}) is None


def test_a_record_without_an_anchor_is_not_reused(tmp_path: Path) -> None:
    """A chunk that cannot prove it continues the chain is re-rendered."""
    video = tmp_path / "chunk.mp4"
    video.write_bytes(b"x")
    pipeline_state = state.State(tmp_path / "state.json")
    pipeline_state.put(state.ChunkRecord(
        index=1, seed=42, prompt_hash="hash", accepted=True,
        video=str(video), last_frame=str(video), anchor="",
    ))

    assert pipeline_state.accepted_for(1, "hash", anchor="frame_a.png") is None


def test_the_state_survives_a_round_trip(tmp_path: Path) -> None:
    path = tmp_path / "state.json"
    video = tmp_path / "chunk.mp4"
    video.write_bytes(b"x")
    pipeline_state = state.State(path)
    pipeline_state.put(state.ChunkRecord(
        index=2, seed=99, prompt_hash="hash", accepted=True,
        video=str(video), last_frame=str(video), anchor="a.png",
        metrics={"face_drift_last": 3.5},
    ))

    reloaded = state.State.load(path)
    record = reloaded.accepted_for(2, "hash", anchor="a.png")
    assert record is not None
    assert record.seed == 99
    assert record.metrics["face_drift_last"] == 3.5


def test_a_damaged_state_file_does_not_stop_the_run(tmp_path: Path) -> None:
    path = tmp_path / "state.json"
    path.write_text("{not json", encoding="utf-8")
    reloaded = state.State.load(path)
    assert reloaded.records() == []


# -------------------------------------------------------------------- gates
def _metrics(**kwargs) -> gates.ChunkMetrics:
    values = {
        "face_drift_mean": 1.0,
        "face_drift_max": 2.0,
        "face_drift_last": 2.0,
        "pore": 5.0e10,
        "texture_ratio": 1.0,
        "texture_measured": True,
    }
    values.update(kwargs)
    return gates.ChunkMetrics(**values)


def test_a_chunk_inside_the_limits_is_accepted() -> None:
    decision = gates.evaluate_chunk(_metrics(), max_face_drift=8.0)
    assert decision.accepted
    assert "limits" in decision.reason


def test_a_chunk_whose_tail_drifted_is_rejected() -> None:
    """The tail is where the face walks away, so that is what is checked."""
    decision = gates.evaluate_chunk(
        _metrics(face_drift_last=12.0, face_drift_mean=3.0),
        max_face_drift=8.0,
    )
    assert not decision.accepted
    assert "tail" in decision.reason


def test_a_chunk_that_lost_its_texture_is_rejected() -> None:
    decision = gates.evaluate_chunk(
        _metrics(texture_ratio=0.4), max_face_drift=8.0, min_texture_ratio=0.75
    )
    assert not decision.accepted
    assert "texture" in decision.reason


def test_the_texture_check_is_skipped_without_a_baseline() -> None:
    """The first chunk has nothing to compare against."""
    metrics = _metrics(texture_ratio=0.0, texture_measured=False)
    assert metrics.pore > 0, "the pore itself was measured"
    decision = gates.evaluate_chunk(
        metrics, max_face_drift=8.0, min_texture_ratio=0.75
    )
    assert decision.accepted


def test_an_unmeasured_texture_fails_the_gate_closed() -> None:
    """A metric that could not be computed is an unknown, not a pass."""
    metrics = _metrics(texture_ratio=0.0)
    metrics.pore = 0.0
    metrics.texture_measured = False
    decision = gates.evaluate_chunk(
        metrics, max_face_drift=8.0, min_texture_ratio=0.75
    )
    assert not decision.accepted
    assert "texture was not measured" in decision.reason


def test_the_best_variant_is_the_one_with_the_least_tail_drift() -> None:
    good = _metrics(face_drift_last=1.0, texture_ratio=1.0)
    bad = _metrics(face_drift_last=9.0, texture_ratio=0.9)
    assert good.score() < bad.score()


# ------------------------------------------------------------------ assemble
def test_the_audio_weld_uses_the_equal_power_curve() -> None:
    """``c1=equal`` is not a curve name and ffmpeg rejects it."""
    graph = ";".join(assemble.build_audio_weld_filters([2.5]))
    assert "c1=qsin" in graph
    assert "c1=equal" not in graph
    assert "[s0][s1]acrossfade" in graph


def test_the_audio_weld_cuts_the_track_at_the_boundaries() -> None:
    graph = ";".join(assemble.build_audio_weld_filters([2.5, 5.0]))
    assert "atrim=start=0:end=2.5" in graph
    assert "atrim=start=2.5:end=5.0" in graph
    assert "atrim=start=5.0" in graph


def test_the_weld_boundaries_are_compensated_for_the_crossfade() -> None:
    """Each crossfade eats 40 ms, so the later cuts move later."""
    boundaries = assemble.audio_boundaries([7.0, 14.0])
    assert boundaries[0] == pytest.approx(7.0)
    assert boundaries[1] == pytest.approx(14.0 + assemble.CROSSFADE_SECONDS)


def test_a_single_chunk_has_no_weld_boundaries() -> None:
    assert assemble.audio_boundaries([]) == []


# --------------------------------------------------------------------- plan
def test_the_plan_mode_prints_a_table_and_renders_nothing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys
) -> None:
    """--plan must not touch ComfyUI, not even to look at it."""
    def explode(*args, **kwargs):  # pragma: no cover - must not run
        raise AssertionError("--plan reached the renderer")

    monkeypatch.setattr(
        "scripts.h3_chain_pipeline.render.render_chunk", explode
    )
    monkeypatch.setattr(
        "scripts.h3_chain_pipeline.paths.comfy_input_dir",
        lambda: (_ for _ in ()).throw(AssertionError("--plan asked for the input dir")),
    )
    monkeypatch.setattr(
        "scripts.h3_chain_pipeline.paths.comfy_output_dir",
        lambda: (_ for _ in ()).throw(AssertionError("--plan asked for the output dir")),
    )

    prompt = tmp_path / "prompt.txt"
    prompt.write_text("a woman with red hair, cinematic", encoding="utf-8")
    image = tmp_path / "still.png"
    image.write_bytes(b"png")
    audio = tmp_path / "track.m4a"
    audio.write_bytes(b"m4a")

    code = main([
        "--plan",
        "--image", str(image),
        "--prompt-file", str(prompt),
        "--audio", str(audio),
        "--output", str(tmp_path / "out.mp4"),
        "--total-frames", "1000",
        "--chunk-frames", "175",
    ])

    assert code == 0
    out = capsys.readouterr().out
    assert "chunk" in out
    assert "seed" in out
    assert "175" in out
    assert not (tmp_path / "out.mp4").exists()


def test_the_plan_table_lists_every_chunk(tmp_path: Path, capsys) -> None:
    prompt = tmp_path / "prompt.txt"
    prompt.write_text("x", encoding="utf-8")
    image = tmp_path / "still.png"
    image.write_bytes(b"png")
    audio = tmp_path / "track.m4a"
    audio.write_bytes(b"m4a")

    main([
        "--plan",
        "--image", str(image),
        "--prompt-file", str(prompt),
        "--audio", str(audio),
        "--output", str(tmp_path / "out.mp4"),
        "--total-frames", "1000",
        "--chunk-frames", "175",
    ])
    out = capsys.readouterr().out
    rows = [line for line in out.splitlines() if line.strip().startswith(("0", "1", "2", "3", "4", "5", "6", "7", "8", "9"))]
    # 1000 frames at 175 per chunk needs six chunks.
    assert len(rows) == 6


def test_the_cli_has_every_documented_flag() -> None:
    parser = build_parser()
    options = {action.dest for action in parser._actions}
    for name in (
        "image", "prompt_file", "audio", "output", "chunk_frames",
        "total_frames", "width", "height", "steps", "seed_base",
        "max_retries", "max_face_drift", "plan", "state", "server",
    ):
        assert name in options


# ------------------------------------------------------------------ assembly
def _ffmpeg() -> str:
    exe = shutil.which("ffmpeg")
    if not exe:
        pytest.skip("ffmpeg is not available")
    return exe


def _frame_count(path: Path) -> int:
    out = subprocess.run(
        [_ffmpeg(), "-v", "error", "-i", str(path), "-f", "null", "-"],
        capture_output=True, text=True, timeout=300,
    )
    assert out.returncode == 0, out.stderr
    counted = subprocess.run(
        ["ffprobe", "-v", "error", "-count_frames", "-select_streams", "v:0",
         "-show_entries", "stream=nb_read_frames", "-of", "csv=p=0", str(path)],
        capture_output=True, text=True, check=True, timeout=300,
    )
    return int(counted.stdout.strip())


def test_the_concatenation_drops_the_duplicated_boundary_frame(tmp_path: Path) -> None:
    """Chunk 1 starts on the last frame of chunk 0, so it appears twice."""
    for index in (0, 1):
        subprocess.run(
            [_ffmpeg(), "-y", "-v", "error", "-f", "lavfi", "-i",
             "testsrc=size=160x120:rate=24:duration=1",
             "-c:v", "libx264", "-crf", "16", "-pix_fmt", "yuv420p",
             str(tmp_path / f"ch{index}.mp4")],
            check=True, timeout=300,
        )
    out = tmp_path / "joined.mp4"

    assemble.concat_chunks(
        [tmp_path / "ch0.mp4", tmp_path / "ch1.mp4"], out, total_frames=0
    )

    # 24 + 24 frames rendered, one duplicated frame dropped.
    assert _frame_count(out) == 47


def test_the_concatenation_is_trimmed_to_the_requested_length(tmp_path: Path) -> None:
    for index in (0, 1):
        subprocess.run(
            [_ffmpeg(), "-y", "-v", "error", "-f", "lavfi", "-i",
             "testsrc=size=160x120:rate=24:duration=1",
             "-c:v", "libx264", "-crf", "16", "-pix_fmt", "yuv420p",
             str(tmp_path / f"ch{index}.mp4")],
            check=True, timeout=300,
        )
    out = tmp_path / "joined.mp4"

    assemble.concat_chunks(
        [tmp_path / "ch0.mp4", tmp_path / "ch1.mp4"], out, total_frames=30
    )

    assert _frame_count(out) == 30


def test_the_audio_weld_produces_a_playable_track(tmp_path: Path) -> None:
    source = tmp_path / "tone.m4a"
    subprocess.run(
        [_ffmpeg(), "-y", "-v", "error", "-f", "lavfi", "-i",
         "sine=frequency=440:duration=6", "-c:a", "aac", "-b:a", "192k",
         str(source)],
        check=True, timeout=300,
    )
    out = tmp_path / "welded.m4a"

    assemble.weld_audio(source, [2.0, 4.0], out, duration=6.0)

    assert out.exists() and out.stat().st_size > 0
    counted = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration",
         "-of", "csv=p=0", str(out)],
        capture_output=True, text=True, check=True, timeout=300,
    )
    assert float(counted.stdout.strip()) == pytest.approx(6.0, abs=0.2)


def test_the_welded_track_is_as_long_as_the_picture(tmp_path: Path) -> None:
    """A track shorter than the picture would cut the clip short."""
    source = tmp_path / "tone.m4a"
    subprocess.run(
        [_ffmpeg(), "-y", "-v", "error", "-f", "lavfi", "-i",
         "sine=frequency=440:duration=3", "-c:a", "aac", "-b:a", "192k",
         str(source)],
        check=True, timeout=300,
    )
    out = tmp_path / "welded.m4a"

    assemble.weld_audio(source, [1.0, 2.0], out, duration=10.0)

    counted = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration",
         "-of", "csv=p=0", str(out)],
        capture_output=True, text=True, check=True, timeout=300,
    )
    assert float(counted.stdout.strip()) == pytest.approx(10.0, abs=0.2)


# --------------------------------------------------------------------- misc
def test_the_package_has_no_absolute_paths() -> None:
    """The guard test in tests/test_no_absolute_paths.py checks the repo;
    this one pins the package to the same rule."""
    package = Path(__file__).resolve().parents[1] / "scripts" / "h3_chain_pipeline"
    offenders: list[str] = []
    for path in sorted(package.glob("*.py")):
        for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            if "://" in line:
                continue
            stripped = line.strip()
            if len(stripped) >= 2 and stripped[1] == ":" and stripped[0].isalpha():
                offenders.append(f"{path.name}:{number}: {stripped}")
    assert offenders == []


def test_the_runner_is_the_repository_one() -> None:
    """The pipeline must not re-implement the renderer."""
    assert paths.RUNNER.name == "h3_i2v_run.py"
    assert paths.RUNNER.exists()
    assert paths.RUNNER.parent.parent == paths.REPO_ROOT
