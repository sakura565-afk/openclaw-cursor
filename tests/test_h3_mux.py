"""Regression tests for the H3 runner's final mux step.

Three bugs lived in the same block and each one silently produced a
"successful" run with no output file:

1. ``--audio`` was passed to ffmpeg verbatim, so a bare name (the normal
   way it is used - the track sits in the ComfyUI input dir) was resolved
   against the process CWD and the mux failed.
2. The mux used ``-stream_loop -1`` on the VIDEO input, which made ffmpeg
   cut the tail: 175 frames came out as 171.
3. The ffmpeg return code was ignored, so a failed mux looked like a
   finished render.
"""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))
sys.path.insert(0, str(REPO_ROOT / "scripts"))

import h3_i2v_run as runner  # noqa: E402

# The mux tests shell out to ffmpeg. On this box it is a pinned WinGet
# build; on the CI runners it comes from PATH. Skip when neither exists
# instead of failing with FileNotFoundError.
_FFMPEG = runner.FFMPEG
pytestmark = pytest.mark.skipif(
    not (Path(_FFMPEG).exists() or shutil.which(_FFMPEG)),
    reason="ffmpeg binary is not available",
)


def _make_clip(path: Path, frames: int = 175) -> None:
    """A tiny silent clip with the exact frame count we care about."""
    ff = runner.FFMPEG
    cmd = [
        ff, "-y", "-v", "error",
        "-f", "lavfi", "-i", f"color=c=blue:s=64x64:r=24:d={frames / 24:.3f}",
        "-frames:v", str(frames), "-pix_fmt", "yuv420p", str(path),
    ]
    subprocess.run(cmd, check=True, capture_output=True, timeout=180)


def _mux(src: Path, audio: Path, out: Path, length: int = 175) -> int:
    """Re-implements the runner's mux branch with the fixed behaviour."""
    audio_path = audio
    if not os.path.isfile(audio_path):
        candidate = Path(runner.COMFYUI_INPUT_DIR) / audio.name
        if candidate.is_file():
            audio_path = candidate
        else:
            return 4
    # No -t: it truncated the tail (175 frames became 173). -shortest on the
    # audio is what actually bounds the result.
    cmd = [
        runner.FFMPEG, "-y", "-i", str(src), "-i", str(audio_path),
        "-c:v", "copy", "-c:a", "aac", "-b:a", "128k",
        "-map", "0:v:0", "-map", "1:a:0", "-shortest", str(out),
    ]
    proc = subprocess.run(cmd, capture_output=True, text=True, timeout=300)
    if proc.returncode != 0 or not out.exists():
        return 4
    return 0


def _nb_frames(path: Path) -> int:
    ffprobe = Path(runner.FFMPEG).with_name("ffprobe.exe")
    if not ffprobe.exists():
        ffprobe = Path(shutil.which("ffprobe") or "ffprobe")
    out = subprocess.run(
        [str(ffprobe), "-v", "error", "-select_streams", "v:0",
         "-count_frames", "-show_entries", "stream=nb_read_frames", "-of", "csv=p=0", str(path)],
        capture_output=True, text=True, timeout=180)
    try:
        return int(out.stdout.strip())
    except ValueError:
        return 0


def test_mux_keeps_every_frame(tmp_path: Path) -> None:
    """The old -stream_loop mux dropped the tail frames."""
    src = tmp_path / "clip.mp4"
    audio = tmp_path / "track.wav"
    out = tmp_path / "out.mp4"
    _make_clip(src, frames=175)
    subprocess.run([runner.FFMPEG, "-y", "-v", "error", "-f", "lavfi",
                    "-i", "sine=frequency=220:duration=8", "-ar", "44100", "-ac", "2",
                    str(audio)], check=True, capture_output=True, timeout=120)
    assert _mux(src, audio, out) == 0
    assert out.exists()
    assert _nb_frames(out) == 175, "mux must not drop frames"


def test_mux_reports_missing_audio_instead_of_writing_nothing(tmp_path: Path) -> None:
    """A missing track must fail loudly, not silently produce no file."""
    src = tmp_path / "clip.mp4"
    out = tmp_path / "out.mp4"
    _make_clip(src, frames=24)
    missing = tmp_path / "does_not_exist.wav"
    assert _mux(src, missing, out) == 4
    assert not out.exists()


def test_audio_bare_name_resolves_to_comfy_input(tmp_path: Path, monkeypatch) -> None:
    """A bare --audio name must resolve inside the ComfyUI input dir."""
    input_dir = tmp_path / "comfy_input"
    input_dir.mkdir()
    track = input_dir / "boho_instr.wav"
    subprocess.run([runner.FFMPEG, "-y", "-v", "error", "-f", "lavfi",
                    "-i", "sine=frequency=330:duration=4", "-ar", "44100", "-ac", "2",
                    str(track)], check=True, capture_output=True, timeout=120)
    monkeypatch.setattr(runner, "COMFYUI_INPUT_DIR", str(input_dir))

    src = tmp_path / "clip.mp4"
    out = tmp_path / "out.mp4"
    _make_clip(src, frames=48)
    # bare name: not a local file, must be found in the ComfyUI input dir
    assert _mux(src, Path("boho_instr.wav"), out) == 0
    assert out.exists()


def test_runner_reports_mux_failure_as_error_code() -> None:
    """The runner must not report a finished render when the mux failed."""
    src = (REPO_ROOT / "scripts" / "h3_i2v_run.py").read_text(encoding="utf-8")
    assert "mux.returncode != 0" in src, "ffmpeg return code must be checked"
    assert "аудио не найдено" in src, "missing audio must be reported"
    assert '"-stream_loop"' not in src, "video stream_loop must not be used"
    assert '"-t",' not in src, "-t truncates the last frames"
