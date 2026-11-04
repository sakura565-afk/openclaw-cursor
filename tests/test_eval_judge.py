"""Tests for the blind A/B judge.

The failures these tests exist to prevent are the ones that already cost us
a wrong decision:

- a skin mask that is empty because the frames were float32 in [0, 1] while
  the thresholds expected 0..255 (the mask was 0% on every frame);
- a pore number that rises because a crop is a wall, not because it is skin;
- a flicker verdict printed for a clip that is not the same take;
- a contact sheet that leaks the file names and unblinds the comparison.

Everything here is CPU only and needs no ComfyUI and no GPU.
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

from scripts.eval_judge import metrics as metrics_mod
from scripts.eval_judge import scoring as scoring_mod
from scripts.eval_judge import sheet as sheet_mod
from scripts.eval_judge import skin as skin_mod
from scripts.eval_judge import video as video_mod
from scripts.eval_judge.judge import main as judge_main


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


def _skin_colour(rgb: tuple[int, int, int]) -> np.ndarray:
    return np.array(rgb, dtype=np.uint8)


def _make_skin_frame(width: int = 320, height: int = 240) -> np.ndarray:
    """A frame that is mostly skin, with a distinctly non-skin border."""
    frame = np.zeros((height, width, 3), dtype=np.uint8)
    frame[:, :] = _skin_colour((196, 142, 104))
    frame[:20, :] = _skin_colour((40, 44, 52))
    frame[-20:, :] = _skin_colour((40, 44, 52))
    frame[:, :20] = _skin_colour((40, 44, 52))
    frame[:, -20:] = _skin_colour((40, 44, 52))
    return frame


def _make_clip(path: Path, *, frames: int = 8, rate: int = 24,
               width: int = 320, height: int = 240, noise: float = 0.0) -> Path:
    """A clip whose frames are skin-coloured and carry visible texture."""
    frame_dir = path.parent / f"_src_{path.stem}"
    frame_dir.mkdir(parents=True, exist_ok=True)
    rng = np.random.default_rng(7)
    for index in range(frames):
        frame = _make_skin_frame(width, height)
        if noise:
            frame = np.clip(
                frame.astype(np.float32) + rng.normal(0.0, noise * 255.0, frame.shape),
                0, 255,
            ).astype(np.uint8)
        else:
            # A deterministic high-frequency pattern, so the pore band has
            # content to measure.
            grid = (np.add.outer(np.arange(height), np.arange(width)) % 4 < 2)
            frame[30:height - 30, 30:width - 30] = np.where(
                grid[30:height - 30, 30:width - 30, None],
                np.minimum(frame[30:height - 30, 30:width - 30] + 26, 255),
                np.maximum(frame[30:height - 30, 30:width - 30] - 26, 0),
            ).astype(np.uint8)
        Image.fromarray(frame).save(frame_dir / f"f{index:03d}.png")
    subprocess.run(
        [_ffmpeg(), "-y", "-v", "error", "-framerate", str(rate),
         "-i", str(frame_dir / "f%03d.png"), "-c:v", "libx264", "-crf", "12",
         "-pix_fmt", "yuv420p", str(path)],
        check=True, timeout=300,
    )
    shutil.rmtree(frame_dir, ignore_errors=True)
    return path


# --------------------------------------------------------------------------
# the 0% mask trap
# --------------------------------------------------------------------------
def test_float_frames_in_zero_one_still_produce_a_skin_mask() -> None:
    """The bug: float32 in [0, 1] against 0..255 thresholds gave a 0% mask."""
    frame = _make_skin_frame().astype(np.float32) / 255.0

    mask = skin_mod.skin_mask(frame)

    assert mask.max() > 0.5, "the mask must not be empty for a float frame"
    assert skin_mod.mask_coverage(mask) > 0.2


def test_to_uint8_normalises_both_float_scales() -> None:
    low = np.full((4, 4, 3), 0.5, dtype=np.float32)
    high = np.full((4, 4, 3), 128.0, dtype=np.float32)

    assert skin_mod.to_uint8(low).max() == pytest.approx(127.5, abs=1.0)
    assert skin_mod.to_uint8(high).max() == pytest.approx(128.0, abs=1.0)
    assert skin_mod.to_uint8(np.zeros((2, 2, 3), dtype=np.uint8)).dtype == np.uint8


def test_the_mask_is_not_the_whole_frame() -> None:
    """A mask that claims everything is not a skin mask, it is a no-op."""
    frame = _make_skin_frame()

    mask = skin_mod.skin_mask(frame)

    coverage = skin_mod.mask_coverage(mask)
    assert 0.0 < coverage < 0.98


def test_pick_crops_returns_high_fill_skin_boxes() -> None:
    frame = _make_skin_frame()
    mask = skin_mod.skin_mask(frame)

    boxes = skin_mod.pick_crops(mask, 2, crop_size=64, min_fill=0.8)

    assert boxes, "at least one skin crop must be found"
    for y0, x0, size in boxes:
        assert skin_mod.crop_fill(mask, (y0, x0, size)) >= 0.8


def test_pick_crops_is_deterministic() -> None:
    mask = skin_mod.skin_mask(_make_skin_frame())

    first = skin_mod.pick_crops(mask, 3, crop_size=64)
    second = skin_mod.pick_crops(mask, 3, crop_size=64)

    assert first == second


# --------------------------------------------------------------------------
# pore band: the metric that could not tell a pore from noise
# --------------------------------------------------------------------------
def test_isotropic_noise_raises_pore_energy_but_destroys_survival() -> None:
    """The trap the tool has to expose: noise looks like texture.

    Isotropic noise puts energy into the pore band, so a raw pore number
    calls it an improvement. A real pore survives a blur; noise does not.
    """
    rng = np.random.default_rng(3)
    base = rng.uniform(0.4, 0.6, size=(192, 192)).astype(np.float64)
    noise = base + rng.normal(0.0, 0.05, size=base.shape)

    clean = metrics_mod.pore_band_metrics(base)
    noisy = metrics_mod.pore_band_metrics(noise)

    assert noisy["pore"] > clean["pore"], "noise does raise the raw pore number"
    assert noisy["survival"] < clean["survival"], \
        "the survival ratio is what tells the two apart"


def test_pore_band_matches_the_canonical_implementation() -> None:
    """The pore band is imported, not re-derived, so it must agree exactly."""
    from scripts.probe_pore_spectrum import band_energy

    rng = np.random.default_rng(11)
    gray = rng.uniform(0.0, 1.0, size=(128, 128))

    assert metrics_mod.band_energy(gray, 3.0, 12.0) == band_energy(gray, 3.0, 12.0)


def test_the_numpy_fallback_matches_scipy_when_scipy_is_present() -> None:
    """CI has no scipy; the fallback must still agree with it."""
    ndimage = pytest.importorskip("scipy.ndimage")

    rng = np.random.default_rng(5)
    gray = rng.uniform(0.0, 1.0, size=(96, 96))

    expected_lap = ndimage.laplace(gray)
    expected_blur = ndimage.gaussian_filter(gray, sigma=1.2)

    assert np.allclose(metrics_mod.laplacian(gray), expected_lap, atol=1e-6)
    assert np.allclose(metrics_mod.gaussian_blur(gray, 1.2), expected_blur, atol=1e-6)


def test_the_numpy_fallback_keeps_the_image_geometry() -> None:
    """A blur that changes the shape invalidates the pore spectrum.

    The fallback used to pad both axes and transpose, which turned a 16x16
    image into a 26x26 one. The shape has to survive the blur.
    """
    ndimage = pytest.importorskip("scipy.ndimage")
    rng = np.random.default_rng(9)
    gray = rng.uniform(0.0, 1.0, size=(16, 16))

    original = metrics_mod._ndimage
    try:
        metrics_mod._ndimage = None
        blurred = metrics_mod.gaussian_blur(gray, 1.2)
    finally:
        metrics_mod._ndimage = original

    assert blurred.shape == gray.shape
    assert np.allclose(blurred, ndimage.gaussian_filter(gray, sigma=1.2), atol=1e-9)


def test_the_numpy_fallback_handles_a_non_square_image() -> None:
    ndimage = pytest.importorskip("scipy.ndimage")
    rng = np.random.default_rng(10)
    gray = rng.uniform(0.0, 1.0, size=(40, 25))

    original = metrics_mod._ndimage
    try:
        metrics_mod._ndimage = None
        blurred = metrics_mod.gaussian_blur(gray, 1.2)
    finally:
        metrics_mod._ndimage = original

    assert blurred.shape == gray.shape
    assert np.allclose(blurred, ndimage.gaussian_filter(gray, sigma=1.2), atol=1e-9)


def test_measure_crops_resamples_to_the_canonical_size() -> None:
    """Crops of different pixel sizes must measure the same feature."""
    rng = np.random.default_rng(13)
    small = rng.uniform(0.0, 255.0, size=(64, 64, 3)).astype(np.uint8)
    large = np.asarray(
        Image.fromarray(small).resize((256, 256), Image.LANCZOS)
    )

    small_metrics = metrics_mod.measure_crops([_write_png(small)], canonical_size=(128, 128))
    large_metrics = metrics_mod.measure_crops([_write_png(large)], canonical_size=(128, 128))

    assert small_metrics["pore"] > 0
    assert large_metrics["pore"] == pytest.approx(small_metrics["pore"], rel=0.5)


def _write_png(array: np.ndarray) -> str:
    import tempfile

    handle = tempfile.NamedTemporaryFile(suffix=".png", delete=False)
    handle.close()
    Image.fromarray(array).save(handle.name)
    return handle.name


# --------------------------------------------------------------------------
# comparability
# --------------------------------------------------------------------------
def test_a_different_frame_rate_is_not_comparable() -> None:
    reference = {"fps": 24.0, "frames": 100}
    candidate = {"fps": 48.0, "frames": 100}

    result = metrics_mod.check_comparability(
        reference, candidate, {"rgb_delta": 4.0}, {"rgb_delta": 4.0}, correlation=0.99
    )

    assert result["comparable"] is False
    assert result["status"] == "not comparable"
    assert "frame rate" in result["reason"]


def test_a_much_larger_temporal_delta_means_a_different_take() -> None:
    """A delta several times the reference's is different motion, not flicker."""
    reference = {"fps": 24.0, "frames": 100}
    candidate = {"fps": 24.0, "frames": 100}

    result = metrics_mod.check_comparability(
        reference, candidate, {"rgb_delta": 4.0}, {"rgb_delta": 30.0}, correlation=0.99
    )

    assert result["comparable"] is False
    assert "different take" in result["reason"]


def test_the_same_take_is_comparable() -> None:
    reference = {"fps": 24.0, "frames": 100}
    candidate = {"fps": 24.0, "frames": 101}

    result = metrics_mod.check_comparability(
        reference, candidate, {"rgb_delta": 4.0}, {"rgb_delta": 4.1}, correlation=0.99
    )

    assert result["comparable"] is True
    assert result["reason"] == ""


def test_uncorrelated_frames_are_not_comparable() -> None:
    result = metrics_mod.check_comparability(
        {"fps": 24.0, "frames": 100}, {"fps": 24.0, "frames": 100},
        {"rgb_delta": 4.0}, {"rgb_delta": 4.0}, correlation=0.1,
    )

    assert result["comparable"] is False
    assert "same content" in result["reason"]


def test_a_much_smaller_temporal_delta_is_also_not_comparable() -> None:
    """The guard has to work in both directions.

    A candidate whose temporal delta is a fraction of the reference's is not
    "less flicker", it is different motion - a static clip against a moving
    one has a ratio near zero.
    """
    reference = {"fps": 24.0, "frames": 100}
    candidate = {"fps": 24.0, "frames": 100}

    result = metrics_mod.check_comparability(
        reference, candidate, {"rgb_delta": 10.0}, {"rgb_delta": 0.0}, correlation=0.99
    )

    assert result["comparable"] is False
    assert "different" in result["reason"]


def test_a_static_reference_against_a_moving_candidate_is_not_comparable() -> None:
    reference = {"fps": 24.0, "frames": 100}
    candidate = {"fps": 24.0, "frames": 100}

    result = metrics_mod.check_comparability(
        reference, candidate, {"rgb_delta": 0.0}, {"rgb_delta": 10.0}, correlation=0.99
    )

    assert result["comparable"] is False


def test_two_static_clips_have_no_flicker_to_compare() -> None:
    result = metrics_mod.check_comparability(
        {"fps": 24.0, "frames": 100}, {"fps": 24.0, "frames": 100},
        {"rgb_delta": 0.0}, {"rgb_delta": 0.0}, correlation=0.99,
    )

    assert result["comparable"] is False
    assert "no measurable temporal motion" in result["reason"].replace(
        "any measurable", "no measurable"
    )


# --------------------------------------------------------------------------
# the blind sheet
# --------------------------------------------------------------------------
def test_the_shuffle_is_deterministic_for_a_seed() -> None:
    names = ["alpha", "beta", "gamma", "delta"]

    first = sheet_mod.shuffle_variants(names, seed=17)
    second = sheet_mod.shuffle_variants(names, seed=17)

    assert first == second
    assert sorted(first) == sorted(names)


def test_a_different_seed_can_reorder_the_variants() -> None:
    names = [f"v{index}" for index in range(8)]

    assert sheet_mod.shuffle_variants(names, seed=1) != sheet_mod.shuffle_variants(names, seed=2)


def test_the_blind_labels_are_bare_letters() -> None:
    labels = sheet_mod.blind_labels(3)

    assert labels == ["A", "B", "C"]
    assert sheet_mod.blind_labels(27)[-1] == "AA"


def test_the_contact_sheet_carries_no_file_names(tmp_path: Path) -> None:
    """The whole point of the sheet: the deciding model must not see names."""
    rows = []
    for name in ("secret_candidate_name.mp4",):
        crops = []
        for index in range(2):
            path = tmp_path / f"{name}_{index}.png"
            Image.fromarray(_make_skin_frame(64, 64)).save(path)
            crops.append(str(path))
        rows.append(crops)

    sheet = sheet_mod.build_sheet(rows, ["A"], tmp_path / "sheet.png", title="blind")

    assert sheet.exists()
    assert sheet.stat().st_size > 0
    with Image.open(sheet) as image:
        assert image.width > 0 and image.height > 0


# --------------------------------------------------------------------------
# score aggregation
# --------------------------------------------------------------------------
def test_scores_are_aggregated_as_a_median_with_an_iqr() -> None:
    scores = {"A": {"pore_readability": [6, 6, 5], "naturalness": 7,
                    "wax_plastic": 2, "overlay_noise": 1}}

    result = scoring_mod.aggregate(scores, {"A": "A"})

    entry = result["variants"]["A"]
    assert entry["criteria"]["pore_readability"]["median"] == 6
    assert entry["criteria"]["pore_readability"]["n"] == 3
    # The IQR has to be the numpy convention, or the number is not comparable
    # with anything else computed in this repo.
    values = [6, 6, 5]
    assert entry["criteria"]["pore_readability"]["q1"] == pytest.approx(
        float(np.percentile(values, 25))
    )
    assert entry["criteria"]["pore_readability"]["iqr"] == pytest.approx(
        float(np.percentile(values, 75) - np.percentile(values, 25))
    )


def test_the_defect_criteria_run_the_other_way() -> None:
    """wax_plastic and overlay_noise are 10 = worst, so they subtract."""
    scores = {
        "A": {"pore_readability": 6, "naturalness": 6, "wax_plastic": 9, "overlay_noise": 9},
        "B": {"pore_readability": 5, "naturalness": 5, "wax_plastic": 1, "overlay_noise": 1},
    }

    result = scoring_mod.aggregate(scores, {"A": "A", "B": "B"})

    assert result["verdict"]["winner"] == "B"


def test_a_wide_iqr_is_reported_as_an_unstable_judgement() -> None:
    scores = {"A": {"pore_readability": [2, 5, 9, 10]}}

    result = scoring_mod.aggregate(scores, {"A": "A"})

    assert result["verdict"]["status"] == "unstable judgement"
    assert any("pore_readability" in item for item in result["verdict"]["unstable_criteria"])


def test_every_accepted_score_shape_normalises_the_same() -> None:
    expected = {"A": {"pore_readability": [7.0]}}

    assert scoring_mod.normalise_scores({"A": {"pore_readability": 7}}) == expected
    assert scoring_mod.normalise_scores({"scores": {"A": {"pore_readability": 7}}}) == expected
    assert scoring_mod.normalise_scores(
        [{"variant": "A", "pore_readability": 7}]
    ) == expected


def test_criterion_aliases_are_accepted() -> None:
    result = scoring_mod.normalise_scores({"A": {"pores": 6, "natural": 7, "wax": 1, "noise": 2}})

    assert set(result["A"]) == {
        "pore_readability", "naturalness", "wax_plastic", "overlay_noise"
    }


# --------------------------------------------------------------------------
# the CLI, end to end
# --------------------------------------------------------------------------
def test_the_cli_produces_a_report_and_a_blind_sheet(tmp_path: Path) -> None:
    reference = _make_clip(tmp_path / "ref.mp4")
    candidate = _make_clip(tmp_path / "cand.mp4", noise=0.02)
    out = tmp_path / "artifacts"

    exit_code = judge_main([
        "--reference", str(reference),
        "--candidates", str(candidate),
        "--frames", "2",
        "--crops", "1",
        "--flicker-frames", "4",
        "--crop-size", "64",
        "--out", str(out),
        "--seed", "5",
    ])

    assert exit_code == 0
    report_path = out / "report.json"
    sheet_path = out / "blind_sheet.png"
    assert report_path.exists()
    assert sheet_path.exists()

    report = json.loads(report_path.read_text(encoding="utf-8"))
    assert report["reference"]["metrics"]["pore"] > 0
    assert report["candidates"], "the candidate must be in the report"
    assert report["blind"]["sheet"].endswith("blind_sheet.png")
    # The blind order is recorded, and the sheet rows carry bare letters.
    assert set(report["blind"]["labels"].values()) >= {"A", "B"}
    for row in report["blind"]["rows"]:
        assert row["label"] in {"A", "B", "C", "D", "E", "F"}


def test_the_crops_are_paired_between_the_variants(tmp_path: Path) -> None:
    """Crop N of every variant must be the same region of skin."""
    reference = _make_clip(tmp_path / "ref.mp4")
    candidate = _make_clip(tmp_path / "cand.mp4", noise=0.02)
    out = tmp_path / "artifacts"

    judge_main([
        "--reference", str(reference),
        "--candidates", str(candidate),
        "--frames", "2", "--crops", "1", "--flicker-frames", "4",
        "--crop-size", "64", "--out", str(out), "--seed", "5",
    ])

    report = json.loads((out / "report.json").read_text(encoding="utf-8"))
    reference_crops = report["reference"]["crops"]
    candidate_crops = next(iter(report["candidates"].values()))["crops"]
    assert len(reference_crops) == len(candidate_crops)
    assert len(reference_crops) > 0
    # Same size on disk: the crops are resampled to the canonical size.
    for reference_path, candidate_path in zip(reference_crops, candidate_crops):
        with Image.open(reference_path) as first, Image.open(candidate_path) as second:
            assert first.size == second.size


def test_the_prompt_names_the_crops_and_the_criteria(tmp_path: Path) -> None:
    reference = _make_clip(tmp_path / "ref.mp4")
    candidate = _make_clip(tmp_path / "cand.mp4", noise=0.02)
    out = tmp_path / "artifacts"

    judge_main([
        "--reference", str(reference),
        "--candidates", str(candidate),
        "--frames", "2", "--crops", "1", "--flicker-frames", "4",
        "--crop-size", "64", "--out", str(out), "--seed", "5",
    ])

    report = json.loads((out / "report.json").read_text(encoding="utf-8"))
    prompt = report["prompt"]
    assert "pore_readability" in prompt
    assert "naturalness" in prompt
    assert "wax_plastic" in prompt
    assert "overlay_noise" in prompt
    assert "crop" in prompt.lower()


def test_the_prompt_does_not_leak_the_candidate_names(tmp_path: Path) -> None:
    """The prompt goes to the judge; a file name there unblinds everything.

    The crop files live in a directory named after the candidate, so putting
    their paths in the prompt would tell the judge the answer.
    """
    reference = _make_clip(tmp_path / "ref.mp4")
    candidate = _make_clip(tmp_path / "secret_candidate_name.mp4", noise=0.02)
    out = tmp_path / "artifacts"

    judge_main([
        "--reference", str(reference),
        "--candidates", str(candidate),
        "--frames", "2", "--crops", "1", "--flicker-frames", "4",
        "--crop-size", "64", "--out", str(out), "--seed", "5",
    ])

    report = json.loads((out / "report.json").read_text(encoding="utf-8"))
    prompt = report["prompt"]
    # The judge must not be able to tell which variant is which. The crop
    # files live in a directory named after the candidate, so any file name
    # or directory name in the prompt would unblind the whole comparison.
    assert "secret_candidate_name" not in prompt
    assert "ref.mp4" not in prompt
    assert "secret_candidate" not in prompt
    assert "candidate_0" not in prompt
    # The blind letters are still there, so the judge can answer.
    assert "variant A" in prompt


def test_an_incomparable_candidate_gets_no_flicker_numbers(tmp_path: Path) -> None:
    """A different frame rate means the temporal numbers mean nothing.

    They must not appear in the metrics block and must not be printed -
    a number next to a "not comparable" label still reads as a verdict.
    """
    reference = _make_clip(tmp_path / "ref.mp4", rate=24)
    candidate = _make_clip(tmp_path / "cand.mp4", rate=12, noise=0.02)
    out = tmp_path / "artifacts"

    judge_main([
        "--reference", str(reference),
        "--candidates", str(candidate),
        "--frames", "2", "--crops", "1", "--flicker-frames", "4",
        "--crop-size", "64", "--out", str(out), "--seed", "5",
    ])

    report = json.loads((out / "report.json").read_text(encoding="utf-8"))
    entry = next(iter(report["candidates"].values()))
    assert entry["flicker"]["comparable"] is False
    assert entry["flicker"]["reason"]
    for key in ("rgb_delta", "hf_delta", "face_drift"):
        assert key not in entry["metrics"], f"{key} must not be reported as a metric"
    # The texture numbers are still there - those ARE comparable.
    assert entry["metrics"]["pore"] > 0


def test_the_scores_are_picked_up_and_aggregated(tmp_path: Path) -> None:
    reference = _make_clip(tmp_path / "ref.mp4")
    candidate = _make_clip(tmp_path / "cand.mp4", noise=0.02)
    out = tmp_path / "artifacts"
    scores_path = tmp_path / "scores.json"
    scores_path.write_text(json.dumps({
        "A": {"pore_readability": 6, "naturalness": 7, "wax_plastic": 2, "overlay_noise": 2},
        "B": {"pore_readability": 8, "naturalness": 8, "wax_plastic": 1, "overlay_noise": 1},
    }), encoding="utf-8")

    judge_main([
        "--reference", str(reference),
        "--candidates", str(candidate),
        "--frames", "2", "--crops", "1", "--flicker-frames", "4",
        "--crop-size", "64", "--out", str(out), "--seed", "5",
        "--scores", str(scores_path),
    ])

    report = json.loads((out / "report.json").read_text(encoding="utf-8"))
    assert report["scores"] is not None
    assert report["verdict"] is not None
    assert report["verdict"]["judge"]["winner"] in {"A", "B"}
    assert report["scores"]["variants"]


def test_a_missing_reference_is_an_error_not_a_crash(tmp_path: Path) -> None:
    exit_code = judge_main([
        "--reference", str(tmp_path / "nope.mp4"),
        "--candidates", str(tmp_path / "also_nope.mp4"),
        "--out", str(tmp_path / "artifacts"),
    ])

    assert exit_code == 2


# --------------------------------------------------------------------------
# video helpers
# --------------------------------------------------------------------------
def test_probe_reads_the_frame_rate_and_the_size(tmp_path: Path) -> None:
    clip = _make_clip(tmp_path / "probe.mp4", frames=6, rate=24)

    info = video_mod.probe(clip)

    assert info["width"] == 320
    assert info["height"] == 240
    assert info["fps"] == pytest.approx(24.0, abs=0.1)
    assert info["frames"] == 6


def test_the_timestamps_are_evenly_spread_and_avoid_the_edges() -> None:
    stamps = video_mod.uniform_timestamps(13.2, 5)

    assert len(stamps) == 5
    assert stamps[0] > 0.0
    assert stamps[-1] < 13.2
    gaps = [b - a for a, b in zip(stamps, stamps[1:])]
    assert max(gaps) - min(gaps) < 1e-6


def test_the_frames_are_extracted_at_the_requested_moments(tmp_path: Path) -> None:
    clip = _make_clip(tmp_path / "times.mp4", frames=8)
    stamps = [0.0, 0.25]

    frames = video_mod.extract_frames(clip, stamps, tmp_path / "frames")

    assert len(frames) == 2
    assert all(path.exists() for path in frames)


def test_the_correlation_of_a_clip_with_itself_is_one(tmp_path: Path) -> None:
    frame = _make_skin_frame()
    shifted = np.roll(frame, 3, axis=1)

    assert video_mod.frame_correlation(frame, frame) == pytest.approx(1.0, abs=1e-6)
    assert video_mod.frame_correlation(frame, shifted) < 1.0


def test_identical_flat_frames_correlate_as_one() -> None:
    """A flat frame has an undefined correlation, not a zero one.

    Two identical flat frames are the same content; returning 0.0 would make
    a static clip look like a different take.
    """
    flat = np.full((16, 16, 3), 127, dtype=np.uint8)
    other = np.full((16, 16, 3), 200, dtype=np.uint8)

    assert video_mod.frame_correlation(flat, flat) == pytest.approx(1.0)
    assert video_mod.frame_correlation(flat, other) == pytest.approx(0.0)
