"""Tests for the structure-based pore injector.

The bugs these lock down are the ones we actually hit:

- the float32 [0, 1] frame trap that made the mask always empty, so every
  strength produced a byte-identical file;
- the constant amplitude floor (detail_floor = 0.35) that smeared noise over
  the whole frame;
- the 85%-coverage mask that put pores in the background;
- a pore field that is really just noise (isotropic, no pits).
"""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
from pathlib import Path

import numpy as np
import pytest
from PIL import Image

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from scripts.pore_injector.field import build_pore_field
from scripts.pore_injector.inject import FlowTracker, inject_pores, structure_map
from scripts.pore_injector.mask import fallback_skin_mask, load_external_mask
from scripts.pore_injector.metrics import RunMetrics, compare_runs


# ---------------------------------------------------------------- helpers

def _skin_frame(height: int = 160, width: int = 240, seed: int = 0) -> np.ndarray:
    """A float32 [0, 1] frame that looks like skin: warm, mid-bright, SMOOTH.

    Deliberately free of per-pixel noise: this is the "flat area" case, and
    it must receive no structure at all.
    """
    ramp = np.linspace(0.55, 0.75, width, dtype=np.float32)[None, :, None]
    vertical = np.linspace(0.0, 0.05, height, dtype=np.float32)[:, None, None]
    rgb = (ramp + vertical) * np.array([1.00, 0.72, 0.58], dtype=np.float32)
    return np.clip(rgb, 0.0, 1.0).astype(np.float32)


def _textured_skin_frame(height: int = 160, width: int = 240, seed: int = 0) -> np.ndarray:
    """Skin WITH high-frequency detail - the case the structure map keys on."""
    rng = np.random.default_rng(seed)
    frame = _skin_frame(height, width, seed)
    noise = rng.standard_normal((height, width)).astype(np.float32)
    noise = np.stack([noise] * 3, axis=-1)
    return np.clip(frame + noise * 0.02, 0.0, 1.0).astype(np.float32)


# ---------------------------------------------------------------- the field

def test_field_is_sparse_not_a_noise_carpet() -> None:
    """A pore field must leave most of the frame untouched.

    An isotropic noise field has energy everywhere; that is what made the eye
    call the first injector "digital sand".
    """
    field, stats = build_pore_field((200, 300), seed=7, pit_density=60.0)

    assert field.shape == (200, 300)
    assert stats["pit_count"] > 0
    # Most pixels must be essentially zero.
    assert float(np.mean(np.abs(field) < 0.05)) > 0.5
    # And the marks must be a small minority of the frame.
    assert stats["coverage_fraction"] < 0.5


def test_field_has_dark_cores_with_brighter_rims() -> None:
    """A pit is a dark depression with a lit rim - that is what reads as skin."""
    field, _ = build_pore_field((200, 300), seed=7, pit_density=60.0, rim=0.45)

    assert field.min() < -0.5, "the field must contain dark pit cores"
    assert field.max() > 0.05, "the field must contain brighter rims"
    # The rim energy must be a real share of the core energy, not a rounding
    # error.
    core = float((np.minimum(field, 0.0) ** 2).sum())
    rim = float((np.maximum(field, 0.0) ** 2).sum())
    assert rim > 0.05 * core


def test_field_is_reproducible_and_seed_dependent() -> None:
    """One seed must give the same field every time, and different seeds
    must give different fields - otherwise the field is regenerated per
    frame and the pores crawl."""
    a, _ = build_pore_field((120, 160), seed=42)
    b, _ = build_pore_field((120, 160), seed=42)
    c, _ = build_pore_field((120, 160), seed=43)

    assert np.array_equal(a, b)
    assert not np.array_equal(a, c)


def test_two_octave_adds_pits_and_is_denser() -> None:
    single, stats_single = build_pore_field((200, 300), seed=7, two_octave=False)
    double, stats_double = build_pore_field((200, 300), seed=7, two_octave=True)

    assert stats_double["pit_count"] > stats_single["pit_count"]
    assert stats_double["coarse_pits"] > 0
    assert not np.array_equal(single, double)


# ---------------------------------------------------------------- the mask

def test_mask_is_not_empty_on_float32_frames() -> None:
    """The float32 trap.

    Frames here are float32 in [0, 1]. Running cvtColor on those treats the
    values as if they were 0..255, which collapses the colour and leaves the
    mask empty - so every strength produced a byte-identical file.
    """
    frame = _skin_frame()
    mask, stats = fallback_skin_mask(frame)

    assert mask.dtype == np.float32
    assert mask.shape == frame.shape[:2]
    assert stats["coverage_pct"] > 10.0, "the mask must not be empty"
    assert float(mask.max()) > 0.5


def test_mask_rejects_bright_desaturated_foam() -> None:
    """Foam is bright AND desaturated; it must not be selected."""
    foam = np.full((120, 160, 3), 0.97, dtype=np.float32)
    foam[:, :, 0] = 0.99
    foam[:, :, 1] = 0.99
    foam[:, :, 2] = 1.0

    mask, stats = fallback_skin_mask(foam)

    assert stats["coverage_pct"] < 5.0


def test_mask_keeps_only_the_largest_region() -> None:
    """Two blobs of skin, one much bigger: only the big one must survive."""
    frame = np.full((200, 300, 3), 0.2, dtype=np.float32)
    frame[20:90, 20:90] = np.array([0.80, 0.58, 0.46], dtype=np.float32)   # big blob
    frame[150:170, 250:270] = np.array([0.80, 0.58, 0.46], dtype=np.float32)  # small blob

    mask, stats = fallback_skin_mask(frame)

    assert stats["coverage_before_largest_pct"] > stats["coverage_pct"]
    assert float(mask[30:60, 30:60].max()) > 0.5, "the big blob must survive"
    assert float(mask[150:170, 250:270].max()) < 0.5, "the small blob must go"


def test_external_mask_is_read_and_resized(tmp_path: Path) -> None:
    """An external PNG mask must be applied to the frames."""
    mask_path = tmp_path / "mask.png"
    Image.fromarray(np.full((100, 100), 255, dtype=np.uint8)).save(mask_path)

    mask, stats = load_external_mask(mask_path, (160, 240))

    assert mask.shape == (160, 240)
    assert stats["mode"] == "external_png"
    assert stats["coverage_pct"] > 99.0


# ------------------------------------------------------- the structure map

def test_structure_map_has_no_constant_floor() -> None:
    """Smooth areas must receive EXACTLY zero.

    The old injector used detail_floor = 0.35, which meant every masked pixel
    got at least 35% of the full amplitude no matter how smooth it was.
    """
    frame = _skin_frame()  # smooth, no texture at all
    mask = np.ones(frame.shape[:2], dtype=np.float32)

    structure = structure_map(frame, mask=mask, threshold=0.55)

    assert float(structure.max()) == 0.0, "a flat frame must get no structure"
    assert float(structure.min()) == 0.0


def test_structure_map_is_high_where_the_frame_has_detail() -> None:
    """Textured skin must score above smooth skin."""
    smooth = _skin_frame(seed=1)
    textured = _textured_skin_frame(seed=1)
    mask = np.ones(smooth.shape[:2], dtype=np.float32)

    a = structure_map(smooth, mask=mask, threshold=0.55)
    b = structure_map(textured, mask=mask, threshold=0.55)

    assert float(b.mean()) > float(a.mean())
    assert float(b.max()) > float(a.max())


def test_structure_map_threshold_zeroes_the_smoothest_region() -> None:
    """Raising the threshold must reduce, not increase, the injected area."""
    frame = _textured_skin_frame(seed=3)
    mask = np.ones(frame.shape[:2], dtype=np.float32)

    low = structure_map(frame, mask=mask, threshold=0.3)
    high = structure_map(frame, mask=mask, threshold=0.9)

    assert float(np.mean(high > 0)) <= float(np.mean(low > 0))


# ---------------------------------------------------------------- injection

def test_injection_changes_the_frame_and_respects_the_mask() -> None:
    frames = [_textured_skin_frame(seed=i) for i in range(3)]
    field, _ = build_pore_field(frames[0].shape[:2], seed=11, pit_density=80.0)
    masks = [np.ones(f.shape[:2], dtype=np.float32) for f in frames]

    out, amounts = inject_pores(frames, field, masks, strength=0.2, use_flow=False)

    assert len(out) == 3
    assert not np.array_equal(out[0], frames[0])
    assert out[0].dtype == np.float32
    assert float(out[0].min()) >= 0.0 and float(out[0].max()) <= 1.0
    assert float(amounts[0].max()) > 0.0


def test_injection_is_a_no_op_on_a_fully_masked_out_frame() -> None:
    """No mask, no injection - otherwise the background gets pores."""
    frames = [_textured_skin_frame(seed=i) for i in range(2)]
    field, _ = build_pore_field(frames[0].shape[:2], seed=11)
    masks = [np.zeros(frames[0].shape[:2], dtype=np.float32) for _ in frames]

    out, amounts = inject_pores(frames, field, masks, strength=0.2, use_flow=False)

    assert np.allclose(out[0], frames[0])
    assert float(amounts[0].max()) == 0.0


def test_strength_zero_changes_nothing() -> None:
    frames = [_textured_skin_frame(seed=i) for i in range(2)]
    field, _ = build_pore_field(frames[0].shape[:2], seed=11)
    masks = [np.ones(frames[0].shape[:2], dtype=np.float32) for _ in frames]

    out, _ = inject_pores(frames, field, masks, strength=0.0, use_flow=False)

    assert np.allclose(out[0], frames[0])


def test_field_is_generated_once_for_the_whole_clip() -> None:
    """The field must be identical for every frame of a clip.

    This is the anti-crawl requirement: BCNodes draws a different random
    field per frame from one seed, which makes the pores crawl by
    construction. The check is that the injected delta is EXACTLY the shared
    field times the per-frame amount - so the field cannot have been
    regenerated in between.
    """
    frames = [_textured_skin_frame(seed=i) for i in range(4)]
    field, _ = build_pore_field(frames[0].shape[:2], seed=11)
    masks = [np.ones(f.shape[:2], dtype=np.float32) for f in frames]

    out, amounts = inject_pores(frames, field, masks, strength=0.2, use_flow=False)

    for index in range(len(out)):
        delta = out[index] - frames[index]
        expected = (field * amounts[index])[:, :, None]
        assert np.allclose(delta, expected, atol=1e-6), \
            "the injected delta must be the ONE shared field times the amount"


def test_temporal_lowpass_slows_the_injection_changes() -> None:
    """A stronger temporal low-pass must make the amount map steadier."""
    frames = [_textured_skin_frame(seed=i) for i in range(8)]
    field, _ = build_pore_field(frames[0].shape[:2], seed=11)
    masks = [np.ones(f.shape[:2], dtype=np.float32) for f in frames]

    _, fast = inject_pores(frames, field, masks, strength=0.2, temporal_sigma=0.0, use_flow=False)
    _, slow = inject_pores(frames, field, masks, strength=0.2, temporal_sigma=8.0, use_flow=False)

    def jitter(amounts):
        return float(np.mean([np.abs(a - b).mean() for a, b in zip(amounts, amounts[1:])]))

    assert jitter(slow) < jitter(fast)


def test_flow_tracker_moves_the_field_with_the_content() -> None:
    """A synthetic shift must move the field by the same amount."""
    height, width = 120, 160
    field = np.zeros((height, width), dtype=np.float32)
    field[60, 80] = 1.0

    tracker = FlowTracker((height, width))
    tracker.update(np.full((height, width), 5.0, dtype=np.float32),
                   np.zeros((height, width), dtype=np.float32))

    warped = tracker.apply(field)
    ys, xs = np.where(warped > 0.5)

    assert len(xs) > 0
    # The marker must have moved right by 5 px.
    assert int(np.median(xs)) - 80 == 5


# ---------------------------------------------------------------- metrics

def test_run_metrics_match_the_batch_comparison() -> None:
    frames = [_textured_skin_frame(seed=i) for i in range(4)]
    altered = [np.clip(f + 0.01, 0, 1) for f in frames]

    streamed = RunMetrics()
    for a, b in zip(frames, altered):
        streamed.update(a, b)
    batch = compare_runs(frames, altered)

    assert streamed.result()["pore_band_energy"]["delta_pct"] == \
        pytest.approx(batch["pore_band_energy"]["delta_pct"])
    assert streamed.result()["temporal_rgb_delta"]["delta_pct"] == \
        pytest.approx(batch["temporal_rgb_delta"]["delta_pct"])


def test_run_metrics_report_real_numbers() -> None:
    """The report must contain values computed from the frames, not zeros."""
    frames = [_textured_skin_frame(seed=i) for i in range(4)]
    rng = np.random.default_rng(5)
    # Adding high-frequency noise must raise the pore band energy.
    altered = [
        np.clip(f + rng.standard_normal(f.shape).astype(np.float32) * 0.01, 0, 1)
        for f in frames
    ]

    result = compare_runs(frames, altered)

    assert result["pore_band_energy"]["input"] > 0.0
    assert result["pore_band_energy"]["output"] > 0.0
    assert result["temporal_rgb_delta"]["input"] >= 0.0
    assert result["frames_compared"] == 4
    # The metric must actually notice the added texture.
    assert result["pore_band_energy"]["output"] > result["pore_band_energy"]["input"]


# ---------------------------------------------------------------- the CLI

def _ffmpeg() -> str:
    exe = shutil.which("ffmpeg")
    if exe is None:
        pytest.skip("ffmpeg is not available")
    return exe


def _make_clip(path: Path, frames: int = 8, width: int = 160, height: int = 120) -> Path:
    subprocess.run(
        [_ffmpeg(), "-y", "-v", "error", "-f", "lavfi",
         "-i", f"testsrc=size={width}x{height}:rate=24:duration={frames / 24:.2f}",
         "-c:v", "libx264", "-crf", "16", "-pix_fmt", "yuv420p", str(path)],
        check=True, timeout=300,
    )
    return path


def test_cli_runs_and_writes_video_and_report(tmp_path: Path) -> None:
    """End to end: a real video in, a real video and a real JSON out."""
    source = _make_clip(tmp_path / "in.mp4")
    out = tmp_path / "out.mp4"

    result = subprocess.run(
        [sys.executable, "scripts/pore_injector/pore_injector.py",
         "--video", str(source), "--out", str(out), "--max-frames", "6"],
        cwd=str(Path(__file__).resolve().parents[1]),
        capture_output=True, text=True, timeout=900,
    )

    assert result.returncode == 0, result.stderr[-2000:]
    assert out.exists() and out.stat().st_size > 0

    report_path = tmp_path / "out_report.json"
    assert report_path.exists(), "a JSON report must be written next to the output"

    report = json.loads(report_path.read_text(encoding="utf-8"))
    assert report["frames_processed"] == 6
    assert report["pore_field"]["pit_count"] > 0
    assert "coverage_pct" in report["mask"]
    # The metrics must be real numbers, not placeholders.
    assert report["metrics"]["pore_band_energy"]["input"] > 0.0
    assert report["metrics"]["pore_band_energy"]["output"] > 0.0
    assert report["metrics"]["temporal_rgb_delta"]["input"] >= 0.0
    assert report["metrics"]["temporal_hf_delta"]["input"] >= 0.0


def test_cli_reports_missing_video_instead_of_crashing(tmp_path: Path) -> None:
    result = subprocess.run(
        [sys.executable, "scripts/pore_injector/pore_injector.py",
         "--video", str(tmp_path / "nope.mp4"), "--out", str(tmp_path / "out.mp4")],
        cwd=str(Path(__file__).resolve().parents[1]),
        capture_output=True, text=True, timeout=300,
    )

    assert result.returncode != 0
    assert "not found" in (result.stderr + result.stdout).lower()


def test_cli_accepts_the_documented_parameters() -> None:
    """Every parameter named in the specification must exist."""
    result = subprocess.run(
        [sys.executable, "scripts/pore_injector/pore_injector.py", "--help"],
        cwd=str(Path(__file__).resolve().parents[1]),
        capture_output=True, text=True, timeout=300,
    )

    assert result.returncode == 0
    for flag in ["--video", "--out", "--strength", "--pit-density", "--rim",
                 "--pore-sigma", "--threshold", "--mask", "--temporal-sigma",
                 "--seed", "--two-octave", "--max-frames", "--rife-out",
                 "--flow", "--no-flow"]:
        assert flag in result.stdout, f"{flag} must be a documented parameter"
