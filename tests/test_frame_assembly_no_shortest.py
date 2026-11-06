"""Guard against the -shortest frame loss in the video assembly scripts.

The pattern: a script writes the picture as a frame sequence and muxes the
source audio next to it. With -shortest the output length comes from the
audio stream, and an AAC stream is never exactly as long as the video, so the
picture loses its last frames and finishes before the music does. The 7.29 s
clip came out with 174 frames instead of 175 in the pore-flow pass, and with
171 instead of 175 in the face-lock pass.

These scripts must pin the frame count and copy the audio.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

REPO = Path(__file__).resolve().parents[1]

# Scripts that assemble a frame sequence and mux the source audio.
FRAME_ASSEMBLERS = [
    "scripts/skin_pore_flow_video.py",
    "scripts/skin_finish_video.py",
    "scripts/skin_sss_bloom.py",
    "scripts/itf_skin_blend_video.py",
    "scripts/cleanup_skin_artifacts.py",
    "scripts/pore_injector/pore_injector.py",
]


def test_frame_assemblers_do_not_use_shortest() -> None:
    offenders = []
    for rel in FRAME_ASSEMBLERS:
        path = REPO / rel
        assert path.is_file(), rel
        text = path.read_text(encoding="utf-8")
        for number, line in enumerate(text.splitlines(), start=1):
            if '"-shortest"' in line and not line.lstrip().startswith("#"):
                offenders.append(f"{rel}:{number}: {line.strip()}")
    assert not offenders, "frame loss pattern is back:\n" + "\n".join(offenders)


def test_frame_assemblers_copy_the_audio() -> None:
    """Re-encoding the audio changes it; copying keeps it bit-identical."""
    offenders = []
    for rel in FRAME_ASSEMBLERS:
        text = (REPO / rel).read_text(encoding="utf-8")
        if '"-c:a", "copy"' not in text:
            offenders.append(rel)
    assert not offenders, "audio is re-encoded in:\n" + "\n".join(offenders)


def test_the_frame_count_is_pinned_where_frames_are_files() -> None:
    """The scripts that read PNG sequences must pin -frames:v."""
    for rel in FRAME_ASSEMBLERS:
        text = (REPO / rel).read_text(encoding="utf-8")
        if 'f_%05d.png' in text or "start_number" in text:
            assert '"-frames:v"' in text, rel
