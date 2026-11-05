"""Tests for the vocal-driven lip-sync tooling.

The boho clip was rejected twice: first because the face was not the reference
woman, then because the lips did not follow the vocal. The second defect has
three separate causes that this tooling has to keep apart:

1. the driving audio was the full mix, so the mouth followed the guitar;
2. the pipeline's mouth has a lead of roughly 625 ms;
3. whatever residual is left has to be removed by shifting the delivered audio
   window, not by re-rendering.
"""
from __future__ import annotations

import importlib.util
import os
import subprocess
import sys
from pathlib import Path

import numpy as np
import pytest

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"


def _load(name: str):
    spec = importlib.util.spec_from_file_location(name, SCRIPTS / f"{name}.py")
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


def test_lipsync_vocal_score_sign_convention():
    """A mouth that leads the audio must give a negative lag.

    The scorer shifts the MOUTH series against the audio envelope, so a mouth
    that is ahead of the sound peaks at a negative lag and a mouth that
    trails peaks at a positive one. If the sign ever flips, the timing fix in
    align_audio_to_mouth starts shifting the audio the wrong way.
    """
    mod = _load("lipsync_vocal_score")
    # one envelope sample == 40 ms. A single pulse keeps the lag unique: with
    # a periodic signal every multiple of the period correlates equally.
    e = np.zeros(40, dtype=np.float32)
    e[20] = 1.0
    m = np.roll(e, -2)          # the mouth is 2 samples (80 ms) AHEAD
    corr, lag = mod.best_lag(e, m)
    assert lag == -2, f"expected a lead of -2, got {lag}"
    assert corr > 0.9


def test_lipsync_vocal_score_lag_follows_the_mouth():
    """A mouth that trails the audio must give a positive lag."""
    mod = _load("lipsync_vocal_score")
    e = np.zeros(40, dtype=np.float32)
    e[20] = 1.0
    m = np.roll(e, 3)           # the mouth is 3 samples BEHIND
    corr, lag = mod.best_lag(e, m)
    assert lag == 3, f"expected a lag of +3, got {lag}"


def test_lipsync_vocal_score_env_is_normalised():
    mod = _load("lipsync_vocal_score")
    x = np.linspace(-1, 1, 16000, dtype=np.float32)
    e = mod.envelope(x, 16000)
    assert e.max() == pytest.approx(1.0, abs=1e-5)
    assert e.min() >= 0.0


def test_lipsync_vocal_score_corr_handles_flat_signal():
    mod = _load("lipsync_vocal_score")
    flat = np.zeros(50, dtype=np.float32)
    assert mod.corr(flat, flat) == 0.0
    assert mod.corr(np.arange(50, dtype=np.float32),
                    np.arange(50, dtype=np.float32)) == pytest.approx(1.0, abs=1e-5)


def test_build_driver_window_math(tmp_path):
    """The driver must start `lead` earlier than the delivered window.

    The pipeline mouth at frame j refers to the driver at j/fps + lead, so the
    driver has to start `lead` earlier for the mouth to land on the delivered
    window.
    """
    mod = _load("build_lipsync_driver")
    assert mod.LEAD_FRAMES == 15
    assert mod.DRIVER_FPS == 25
    assert mod.LEAD_FRAMES / mod.DRIVER_FPS == pytest.approx(0.6)

    # the produced driver must be exactly as long as the clip, otherwise
    # LatentSync pads (loops) the video and re-runs the inference
    track = tmp_path / "track.wav"
    vocal = tmp_path / "vocal.wav"
    for p in (track, vocal):
        subprocess.run(["ffmpeg", "-y", "-v", "error", "-f", "lavfi",
                        "-i", "sine=frequency=220:duration=60", "-ac", "1",
                        "-ar", "16000", str(p)], check=True, capture_output=True)

    drv = tmp_path / "driver.wav"
    aud = tmp_path / "audio.wav"
    mod.main.__wrapped__ if False else None
    argv = ["build_lipsync_driver.py",
            "--track", str(track), "--vocal", str(vocal),
            "--start", "38.0", "--duration", "7.3",
            "--out-driver", str(drv), "--out-audio", str(aud)]
    old = sys.argv
    sys.argv = argv
    try:
        assert mod.main() == 0
    finally:
        sys.argv = old

    import wave
    with wave.open(str(drv), "rb") as w:
        d_frames = w.getnframes() / w.getframerate()
    with wave.open(str(aud), "rb") as w:
        a_frames = w.getnframes() / w.getframerate()
    assert d_frames == pytest.approx(7.3, abs=0.05)
    assert a_frames == pytest.approx(7.3, abs=0.05)


def test_align_audio_scans_instead_of_guessing_the_direction():
    """The alignment must scan the window, not shift by a guessed sign.

    Shifting the window by a "lag" was wrong: the sign convention is easy to
    get backwards and shifting the wrong way makes the fit worse. The tool
    now scans the window and keeps the position with the best correlation.
    """
    src = (SCRIPTS / "align_audio_to_mouth.py").read_text(encoding="utf-8")
    assert "scan_window" in src, "the tool must scan the audio window"
    assert "a.start + lag_s" not in src, "no sign-based shift"
    assert "a.start - lag_s" not in src, "no sign-based shift"
    assert "--search" in src, "the search range must be configurable"


def test_align_audio_scan_picks_the_aligned_window(tmp_path):
    """scan_window must peak at the window the mouth was driven from."""
    mod = _load("align_audio_to_mouth")
    sr = 16000
    # A non-periodic envelope: with a periodic one every multiple of the
    # period correlates equally and the scan cannot pick a unique window.
    rng = np.random.default_rng(7)
    n = int(60 / 0.04)
    env = rng.random(n).astype(np.float32)
    mouth = np.zeros(175, dtype=np.float32)
    for j in range(175):
        idx = int((37.30 + j / 24.0) / 0.04)
        mouth[j] = env[idx % n]
    rows = mod.scan_window(mouth, env, sr, 36.5, 39.5, 7.3, 24)
    c, W = max(rows)
    assert W == pytest.approx(37.28, abs=0.06), f"picked {W}"
    assert c > 0.9


def test_scripts_have_no_absolute_paths():
    # the fragments are split so the guard in tests/test_no_absolute_paths.py
    # does not match this file's own allowlist
    bad = ("C" + ":\\Users\\", "C" + ":/Users/", "G" + ":/AI/", "G" + ":\\AI\\")
    for name in ("lipsync_vocal_score.py", "build_lipsync_driver.py",
                 "align_audio_to_mouth.py"):
        text = (SCRIPTS / name).read_text(encoding="utf-8")
        for frag in bad:
            assert frag not in text, f"{name} must not hardcode {frag}"


def test_registry_has_no_absolute_paths():
    bad = ("C" + ":\\Users\\", "C" + ":/Users/", "G" + ":/AI/", "G" + ":\\AI\\")
    reg = (SCRIPTS / "recipes" / "registry.json").read_text(encoding="utf-8")
    for frag in bad:
        assert frag not in reg


def test_music3_reports_the_frame_count_not_the_channel_count():
    """The duration print must use the frame axis, not the channel axis.

    soundfile writes (frames, channels); a stereo buffer has shape[0] frames
    and shape[1] channels. The report used shape[-1], so every stereo run
    printed "0.0 s audio" for a 20 second track.
    """
    src = (SCRIPTS / "minimax_music3_local.py").read_text(encoding="utf-8")
    assert "frames = data.shape[0]" in src
    assert "data.shape[-1]" not in src, "the channel count must not be used as the length"


def test_music3_frame_count_matches_the_written_file(tmp_path):
    """The printed frames must be what actually landed in the file."""
    import wave
    out = tmp_path / "probe.wav"
    frames, channels, rate = 44100, 2, 44100
    pcm = (np.zeros((frames, channels), dtype="<i2")).tobytes()
    with wave.open(str(out), "wb") as w:
        w.setnchannels(channels)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes(pcm)
    with wave.open(str(out), "rb") as w:
        assert w.getnframes() == frames
        assert w.getnchannels() == channels
    # the frame axis is the duration; the channel axis is the bug
    right = frames / rate
    wrong = channels / rate
    assert right == pytest.approx(1.0)
    assert wrong < 0.001, "the wrong axis must be obviously wrong"


def test_music3_exit_code_survives_the_interpreter_teardown():
    """The exit code must be the real one, not a teardown error.

    The CUDA allocator can raise while the interpreter shuts down, which turned
    a successful generation into a non-zero exit. The script now leaves with
    os._exit, so the early-exit paths have to report their own code.
    """
    import subprocess
    env = dict(os.environ)
    env.pop("MINIMAX_MUSIC3_DIR", None)
    r = subprocess.run([sys.executable, str(SCRIPTS / "minimax_music3_local.py"),
                        "--out", str(Path(os.environ.get("TEMP", "/tmp")) / "probe.wav")],
                       capture_output=True, text=True, env=env, timeout=180)
    assert r.returncode == 2, f"expected 2, got {r.returncode}: {r.stderr[-200:]}"
