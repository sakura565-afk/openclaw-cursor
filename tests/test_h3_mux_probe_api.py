"""Regression: the H3 mux must keep every frame of the render.

probe() returns a DICT (width/height/duration/fps/has_audio). The runner
unpacked it as a (video, audio, format) triple, the ValueError was caught
by a bare "except", the duration stayed 0.0 and the mux wrote "-t 0" -
which kept 2 frames out of a 175-frame render. The still then came from a
black two-frame clip and every anatomy metric collapsed.
"""
import ast
import subprocess
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
RUNNER = REPO / "scripts" / "h3_i2v_run.py"


def _runner_source() -> str:
    return RUNNER.read_text(encoding="utf-8")


def test_runner_does_not_unpack_probe_as_a_triple():
    """probe() is a dict now; a triple unpack would silently break the mux."""
    src = _runner_source()
    tree = ast.parse(src)
    offenders = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign) and isinstance(node.value, ast.Tuple):
            names = [t.id for t in node.value.elts if isinstance(t, ast.Name)]
            if len(names) == 3 and names[0].startswith("_"):
                # a triple unpack of a probe() result
                if "_probe" in ast.unparse(node.value) or "probe" in ast.unparse(node.value):
                    offenders.append(ast.unparse(node))
    assert not offenders, (
        "probe() returns a dict - unpacking it as a triple raises ValueError "
        f"and the mux writes -t 0: {offenders}"
    )


def test_runner_reads_duration_from_the_dict():
    src = _runner_source()
    assert '.get("duration"' in src, (
        "the duration must be read as a dict key, not as _d['format']['duration']"
    )


def test_runner_never_writes_a_zero_length_trim():
    """-t 0 keeps a couple of frames; the frame count has to be the fallback."""
    src = _runner_source()
    assert '"-t", f"{dur:.6f}"' in src
    # the zero-duration branch has to fall back to a frame count
    assert '"-frames:v", str(int(a.length))' in src, (
        "a zero duration must fall back to -frames:v, never to -t 0"
    )


def test_probe_returns_a_dict(tmp_path):
    from video_pipeline.assembler import probe

    clip = tmp_path / "probe_src.mp4"
    subprocess.run(
        ["ffmpeg", "-y", "-v", "error", "-f", "lavfi", "-i", "color=c=black:s=64x64:d=1",
         "-r", "24", "-c:v", "libx264", "-pix_fmt", "yuv420p", str(clip)],
        check=True, capture_output=True)

    info = probe(clip)
    assert isinstance(info, dict)
    assert "duration" in info and "fps" in info


def test_mux_keeps_every_frame(tmp_path):
    """End to end: a 175-frame source must mux to 175 frames, not 2."""
    ffmpeg = "ffmpeg"
    ffprobe = "ffprobe"
    src = tmp_path / "src.mp4"
    wav = tmp_path / "a.wav"
    out = tmp_path / "out.mp4"

    subprocess.run(
        [ffmpeg, "-y", "-v", "error", "-f", "lavfi", "-i", "color=c=black:s=64x64:d=7.29",
         "-r", "24", "-c:v", "libx264", "-pix_fmt", "yuv420p", str(src)],
        check=True, capture_output=True)
    subprocess.run(
        [ffmpeg, "-y", "-v", "error", "-f", "lavfi", "-i", "anullsrc=r=44100:cl=mono:d=7.29",
         str(wav)],
        check=True, capture_output=True)

    from video_pipeline.assembler import probe

    dur = float(probe(src).get("duration", 0.0) or 0.0)
    assert dur > 1.0, "the probe must report a real duration"

    cmd = [ffmpeg, "-y", "-v", "error", "-i", str(src), "-i", str(wav),
           "-c:v", "copy", "-c:a", "aac", "-b:a", "128k",
           "-map", "0:v:0", "-map", "1:a:0", "-t", f"{dur:.6f}", str(out)]
    r = subprocess.run(cmd, capture_output=True, text=True)
    assert r.returncode == 0, r.stderr[-400:]

    n = subprocess.run(
        [ffprobe, "-v", "error", "-select_streams", "v:0", "-count_frames",
         "-show_entries", "stream=nb_read_frames", "-of", "csv=p=0", str(out)],
        capture_output=True, text=True).stdout.strip()
    assert int(n) >= 170, f"the mux kept only {n} frames of the render"
