"""Regression test for the frame loss in the face-lock assembly.

The assembly used -shortest, so the length came from the audio stream. An
AAC stream is never exactly as long as the video: the 7.29 s clip came out
with 171 frames instead of 175 and the picture finished before the music.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from scripts.face_lock import build_encode_command  # noqa: E402


def test_the_frame_count_is_pinned() -> None:
    cmd = build_encode_command("frames", "clip.mp4", "out.mp4", 24.0, 175)
    assert "-frames:v" in cmd
    assert cmd[cmd.index("-frames:v") + 1] == "175"
    assert "-shortest" not in cmd


def test_without_a_count_there_is_no_shortest_flag() -> None:
    cmd = build_encode_command("frames", "clip.mp4", "out.mp4", 24.0)
    assert "-shortest" not in cmd


def test_the_audio_is_copied_not_reencoded() -> None:
    cmd = build_encode_command("frames", "clip.mp4", "out.mp4", 24.0, 175)
    assert "copy" in cmd
    assert "0:v:0" in cmd
    assert "1:a:0?" in cmd
