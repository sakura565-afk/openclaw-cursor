"""Tests for the chained-H3 runner.

The chain runner is the answer to the tail drift: every chunk starts on a
supplied frame and the frame it ended on becomes the first frame of the next
chunk. The two mechanics that can silently corrupt the result - the audio
weld and the boundary trim - are checked here on synthetic media, because
both failed at least once while the graph looked plausible.
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

from scripts.h3_chain_video import build_audio_weld_filters, weld_audio


def _ffmpeg() -> str:
    exe = shutil.which("ffmpeg")
    if not exe:
        pytest.skip("ffmpeg is not available")
    return exe


def _ffprobe() -> str:
    exe = shutil.which("ffprobe")
    if not exe:
        pytest.skip("ffprobe is not available")
    return exe


def _duration(path: Path) -> float:
    out = subprocess.run(
        [_ffprobe(), "-v", "error", "-show_entries", "format=duration",
         "-of", "csv=p=0", str(path)],
        capture_output=True, text=True, check=True, timeout=120)
    return float(out.stdout.strip())


def test_weld_filters_use_a_curve_ffmpeg_accepts() -> None:
    graph = ";".join(build_audio_weld_filters([56, 112]))

    # "equal" is not a curve name in ffmpeg and is rejected outright.
    assert "c1=equal" not in graph
    assert "c1=qsin" in graph
    # the labels keep their brackets: [s0][s1]acrossfade=...
    assert "[s0][s1]acrossfade" in graph
    assert "[x1][s2]acrossfade" in graph


def test_weld_filters_cut_the_track_at_the_boundaries() -> None:
    graph = ";".join(build_audio_weld_filters([56, 112]))

    assert "atrim=start=0:end=56" in graph
    assert "atrim=start=56:end=112" in graph
    assert "atrim=start=112" in graph


def test_weld_audio_produces_a_playable_track(tmp_path: Path) -> None:
    src = tmp_path / "tone.m4a"
    subprocess.run(
        [_ffmpeg(), "-y", "-v", "error", "-f", "lavfi", "-i",
         "sine=frequency=440:duration=6", "-c:a", "aac", "-b:a", "192k", str(src)],
        check=True, timeout=300)
    out = tmp_path / "welded.m4a"

    weld_audio(src, [56, 112], out)

    assert out.exists() and out.stat().st_size > 0
    # 6 s of source plus the 0.5 s pad the graph adds.
    assert _duration(out) == pytest.approx(6.5, abs=0.3)


def test_weld_without_boundaries_is_a_plain_copy(tmp_path: Path) -> None:
    src = tmp_path / "tone.m4a"
    subprocess.run(
        [_ffmpeg(), "-y", "-v", "error", "-f", "lavfi", "-i",
         "sine=frequency=440:duration=2", "-c:a", "aac", "-b:a", "192k", str(src)],
        check=True, timeout=300)
    out = tmp_path / "plain.m4a"

    weld_audio(src, [], out)

    assert out.exists()
    assert _duration(out) == pytest.approx(2.0, abs=0.2)


def test_trimmed_concat_drops_the_duplicated_boundary_frame(tmp_path: Path) -> None:
    """The next chunk's first frame duplicates the previous chunk's last one."""
    for i in (1, 2):
        subprocess.run(
            [_ffmpeg(), "-y", "-v", "error", "-f", "lavfi", "-i",
             f"testsrc=size=320x240:rate=24:duration=2", "-c:v", "libx264", "-crf", "16",
             "-pix_fmt", "yuv420p", str(tmp_path / f"ch{i}.mp4")],
            check=True, timeout=300)
    trimmed = tmp_path / "trim2.mp4"
    subprocess.run(
        [_ffmpeg(), "-y", "-v", "error", "-i", str(tmp_path / "ch2.mp4"),
         "-vf", "select=gte(n\\,1)", "-an", "-c:v", "libx264", "-crf", "16",
         "-pix_fmt", "yuv420p", str(trimmed)],
        check=True, timeout=300)
    listing = tmp_path / "concat.txt"
    listing.write_text(
        f"file '{(tmp_path / 'ch1.mp4').as_posix()}'\n"
        f"file '{trimmed.as_posix()}'\n", encoding="utf-8")
    out = tmp_path / "cat.mp4"
    subprocess.run(
        [_ffmpeg(), "-y", "-v", "error", "-f", "concat", "-safe", "0", "-i", str(listing),
         "-c", "copy", str(out)], check=True, timeout=300)

    counted = subprocess.run(
        [_ffprobe(), "-v", "error", "-count_frames", "-select_streams", "v:0",
         "-show_entries", "stream=nb_read_frames", "-of", "csv=p=0", str(out)],
        capture_output=True, text=True, check=True, timeout=300)

    assert int(counted.stdout.strip()) == 95  # 48 + 47
