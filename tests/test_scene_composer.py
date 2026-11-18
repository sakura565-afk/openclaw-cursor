"""Tests for scripts/scene_composer.py.

Uses only synthetic images (numpy) so the suite is self-contained and fast
(< 60s). ffmpeg is required; tests are skipped if it is unavailable.
"""

from __future__ import annotations

import importlib.util
import subprocess
import sys
from pathlib import Path

import numpy as np
import pytest

SCRIPTS_DIR = Path(__file__).resolve().parent.parent / "scripts"
sys.path.insert(0, str(SCRIPTS_DIR))

cv2 = pytest.importorskip("cv2")
import scene_composer as sc  # noqa: E402


def _have_ffmpeg() -> bool:
    try:
        sc.ffmpeg_binary()
        return True
    except RuntimeError:
        return False


pytestmark = pytest.mark.skipif(not _have_ffmpeg(), reason="ffmpeg not available")


# --------------------------------------------------------------------------
# Synthetic image helpers
# --------------------------------------------------------------------------

def _gradient_image(width: int = 1400, height: int = 900) -> np.ndarray:
    """Diagonal RGB-ish gradient as a BGR uint8 array."""
    xs = np.linspace(0, 255, width, dtype=np.float32)
    ys = np.linspace(0, 255, height, dtype=np.float32)
    grid_x, grid_y = np.meshgrid(xs, ys)
    blue = grid_x
    green = grid_y
    red = (grid_x + grid_y) / 2.0
    return np.dstack([blue, green, red]).astype(np.uint8)


def _center_stripe_image(width: int = 1400, height: int = 900) -> np.ndarray:
    """Dark image with a bright vertical stripe at the horizontal center."""
    img = np.zeros((height, width, 3), dtype=np.uint8)
    cx = width // 2
    half = 3
    img[:, cx - half : cx + half, :] = 255
    return img


def _write_png(path: Path, image: np.ndarray) -> Path:
    assert cv2.imwrite(str(path), image)
    return path


def _probe_duration(path: Path) -> float:
    out = subprocess.run(
        [
            "ffprobe",
            "-v",
            "error",
            "-show_entries",
            "format=duration",
            "-of",
            "default=nokey=1:noprint_wrappers=1",
            str(path),
        ],
        capture_output=True,
        text=True,
    )
    return float(out.stdout.strip())


def _stream_info(path: Path) -> dict:
    """Return {'width','height','codec','has_audio'} via ffprobe."""
    out = subprocess.run(
        [
            "ffprobe",
            "-v",
            "error",
            "-show_entries",
            "stream=codec_type,codec_name,width,height",
            "-of",
            "csv=p=0",
            str(path),
        ],
        capture_output=True,
        text=True,
    )
    width = height = 0
    codec = ""
    has_audio = False
    for line in out.stdout.strip().splitlines():
        parts = line.split(",")
        # Field order in ffprobe CSV is not guaranteed, so detect by content.
        if "video" in parts:
            nums = [int(p) for p in parts if p.isdigit()]
            names = [p for p in parts if p not in ("video", "audio") and not p.isdigit()]
            codec = names[0] if names else ""
            if len(nums) >= 2:
                width, height = nums[0], nums[1]
        elif "audio" in parts:
            has_audio = True
    return {"width": width, "height": height, "codec": codec, "has_audio": has_audio}


# --------------------------------------------------------------------------
# Preset loading
# --------------------------------------------------------------------------

def test_presets_load_all_five():
    presets = sc.load_presets()
    for name in ("slow_zoom", "dramatic_zoom", "side_pan", "dolly_in", "dolly_out"):
        assert name in presets
        assert {"zoom_start", "zoom_end", "pan_x", "pan_y"} <= set(presets[name])


def test_yaml_config_matches_builtins():
    """The shipped YAML should define the same preset names as the fallback."""
    yaml_presets = sc.load_presets(sc._PRESETS_PATH)
    assert set(yaml_presets) == set(sc._BUILTIN_PRESETS)


# --------------------------------------------------------------------------
# compose(): valid MP4, correct duration and dimensions
# --------------------------------------------------------------------------

def test_compose_produces_valid_mp4(tmp_path):
    src = _write_png(tmp_path / "in.png", _gradient_image())
    out = tmp_path / "out.mp4"
    composer = sc.SceneComposer(
        src, out, motion="slow_zoom", duration_s=3.0, fps=12, resolution=(640, 400)
    )
    result = composer.compose()

    assert out.is_file()
    assert result.frames == 36  # 3s * 12fps
    assert result.output_path == out

    info = _stream_info(out)
    assert info["codec"] == "h264"
    assert (info["width"], info["height"]) == (640, 400)  # no aspect drift
    assert info["has_audio"] is False  # silent by default

    duration = _probe_duration(out)
    assert abs(duration - 3.0) <= 0.5


def test_aspect_ratio_preserved_odd_source(tmp_path):
    """1280x801-style source must yield exactly the requested 1280x800."""
    src = _write_png(tmp_path / "odd.png", _gradient_image(1280, 801))
    out = tmp_path / "odd.mp4"
    result = sc.SceneComposer(
        src, out, motion="slow_zoom", duration_s=1.0, fps=12, resolution=(1280, 800)
    ).compose()
    assert result.resolution == (1280, 800)
    info = _stream_info(out)
    assert (info["width"], info["height"]) == (1280, 800)


# --------------------------------------------------------------------------
# _apply_motion(): zoom and pan behaviour
# --------------------------------------------------------------------------

def test_apply_motion_zoom_changes_content(tmp_path):
    composer = sc.SceneComposer(
        tmp_path / "x.png", tmp_path / "x.mp4", motion="dramatic_zoom", resolution=(640, 400)
    )
    base = composer._prepare_base(_gradient_image(), (640, 400))

    start = composer._apply_motion(base, "dramatic_zoom", 0.0, 10.0)
    end = composer._apply_motion(base, "dramatic_zoom", 10.0, 10.0)

    assert start.shape == (400, 640, 3)
    assert end.shape == (400, 640, 3)
    # The zoomed-in end frame must differ meaningfully from the start frame.
    assert float(np.mean(np.abs(start.astype(int) - end.astype(int)))) > 1.0


def test_apply_motion_zoom_crop_window_shrinks(tmp_path):
    """Higher zoom must sample a smaller crop window from the base image."""
    composer = sc.SceneComposer(
        tmp_path / "x.png", tmp_path / "x.mp4", motion="dramatic_zoom", resolution=(640, 400)
    )
    base = np.zeros((500, 800, 3), dtype=np.uint8)
    # Mark a horizontal gradient so we can detect how much is visible.
    base[:] = np.linspace(0, 255, 800, dtype=np.uint8)[None, :, None]

    z1 = composer.presets["dramatic_zoom"]["zoom_start"]  # 1.0
    z2 = composer.presets["dramatic_zoom"]["zoom_end"]  # 1.4
    assert z2 > z1
    # At higher zoom the value spread across a row is smaller (less of the
    # gradient is visible before being stretched to the output width).
    start = composer._apply_motion(base, "dramatic_zoom", 0.0, 10.0)
    end = composer._apply_motion(base, "dramatic_zoom", 10.0, 10.0)
    spread_start = int(start[200].max()) - int(start[200].min())
    spread_end = int(end[200].max()) - int(end[200].min())
    assert spread_end < spread_start


def test_side_pan_shifts_center(tmp_path):
    src = _center_stripe_image()
    composer = sc.SceneComposer(
        tmp_path / "p.png", tmp_path / "p.mp4", motion="side_pan", resolution=(1280, 800)
    )
    base = composer._prepare_base(src, (1280, 800))

    start = composer._apply_motion(base, "side_pan", 0.0, 10.0)
    end = composer._apply_motion(base, "side_pan", 10.0, 10.0)

    def stripe_column(frame: np.ndarray) -> int:
        col_sums = frame.astype(np.int64).sum(axis=(0, 2))
        return int(np.argmax(col_sums))

    shift = abs(stripe_column(start) - stripe_column(end))
    # pan_x = 40 -> the stripe should travel ~40 px between first and last frame.
    assert 25 <= shift <= 55


# --------------------------------------------------------------------------
# Size budget + all presets
# --------------------------------------------------------------------------

def test_size_under_10mb_for_10s_1280x800(tmp_path):
    src = _write_png(tmp_path / "big.png", _gradient_image())
    out = tmp_path / "big.mp4"
    result = sc.SceneComposer(
        src, out, motion="slow_zoom", duration_s=10.0, fps=24, resolution=(1280, 800)
    ).compose()

    assert result.frames == 240
    assert result.file_size_mb < sc.TELEGRAM_LIMIT_MB
    assert abs(_probe_duration(out) - 10.0) <= 0.5


@pytest.mark.parametrize(
    "motion", ["slow_zoom", "dramatic_zoom", "side_pan", "dolly_in", "dolly_out"]
)
def test_all_presets_render(tmp_path, motion):
    src = _write_png(tmp_path / f"{motion}.png", _gradient_image(800, 600))
    out = tmp_path / f"{motion}.mp4"
    result = sc.SceneComposer(
        src, out, motion=motion, duration_s=2.0, fps=12, resolution=(640, 400)
    ).compose()
    assert out.is_file()
    assert result.file_size_mb < sc.TELEGRAM_LIMIT_MB
    info = _stream_info(out)
    assert (info["width"], info["height"]) == (640, 400)


# --------------------------------------------------------------------------
# Music bed
# --------------------------------------------------------------------------

def test_music_track_present(tmp_path):
    src = _write_png(tmp_path / "m.png", _gradient_image(800, 600))
    music = tmp_path / "bed.wav"
    # Generate a 5s sine tone with ffmpeg as a stand-in music bed.
    subprocess.run(
        [
            "ffmpeg",
            "-y",
            "-f",
            "lavfi",
            "-i",
            "sine=frequency=440:duration=5",
            str(music),
        ],
        capture_output=True,
        check=True,
    )
    out = tmp_path / "reel.mp4"
    sc.SceneComposer(
        src,
        out,
        motion="side_pan",
        duration_s=3.0,
        fps=12,
        resolution=(640, 400),
        music=music,
    ).compose()

    info = _stream_info(out)
    assert info["has_audio"] is True
    # -shortest should clamp to the 3s video, not the 5s audio.
    assert abs(_probe_duration(out) - 3.0) <= 0.6


# --------------------------------------------------------------------------
# Validation / errors
# --------------------------------------------------------------------------

def test_unknown_motion_raises(tmp_path):
    with pytest.raises(ValueError):
        sc.SceneComposer(tmp_path / "a.png", tmp_path / "a.mp4", motion="nope")


def test_missing_input_raises(tmp_path):
    composer = sc.SceneComposer(
        tmp_path / "missing.png", tmp_path / "a.mp4", duration_s=1.0, fps=12
    )
    with pytest.raises(FileNotFoundError):
        composer.compose()


def test_cli_smoke(tmp_path):
    src = _write_png(tmp_path / "cli.png", _gradient_image(800, 600))
    out = tmp_path / "cli.mp4"
    rc = sc.main(
        [
            str(src),
            "--motion",
            "slow_zoom",
            "--duration",
            "2",
            "--fps",
            "12",
            "--resolution",
            "640",
            "400",
            "--output",
            str(out),
        ]
    )
    assert rc == 0
    assert out.is_file()


def test_importable_module_spec():
    """scene_composer imports cleanly as a standalone module."""
    spec = importlib.util.spec_from_file_location(
        "scene_composer_check", SCRIPTS_DIR / "scene_composer.py"
    )
    assert spec is not None
