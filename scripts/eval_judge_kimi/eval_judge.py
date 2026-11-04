"""Blind A/B judge for skin texture and temporal stability."""

from __future__ import annotations

import argparse
import json
import random
import sys
import tempfile
from numbers import Real
from pathlib import Path

import cv2
import numpy as np
from PIL import Image, ImageDraw

from scripts.compare_video_texture import measure as texture_measure
from scripts.probe_pore_spectrum import band_energy


def skin_mask(frame_rgb: np.ndarray) -> np.ndarray:
    """Return a non-empty-checkable uint8 HSV/YCrCb skin prior."""
    if frame_rgb.dtype != np.uint8:
        raise ValueError("skin mask requires uint8 RGB frames")
    if frame_rgb.ndim != 3 or frame_rgb.shape[2] != 3:
        raise ValueError("skin mask requires an HxWx3 RGB frame")
    hsv = cv2.cvtColor(frame_rgb, cv2.COLOR_RGB2HSV)
    ycrcb = cv2.cvtColor(frame_rgb, cv2.COLOR_RGB2YCrCb)
    h, s, v = cv2.split(hsv)
    _, cr, cb = cv2.split(ycrcb)
    hsv_prior = (h <= 25) & (s >= 35) & (s <= 230) & (v >= 45)
    ycrcb_prior = (cr >= 133) & (cr <= 180) & (cb >= 70) & (cb <= 135)
    mask = ((hsv_prior | ycrcb_prior) & (v < 250)).astype(np.uint8) * 255
    kernel = np.ones((3, 3), dtype=np.uint8)
    return cv2.morphologyEx(mask, cv2.MORPH_OPEN, kernel)


def select_skin_crops(
    frame_rgb: np.ndarray, count: int, canonical_size: int = 128
) -> list[np.ndarray]:
    """Choose high-coverage, spatially separated skin crops at one scale."""
    if count < 1 or canonical_size < 16:
        raise ValueError("crop count must be positive and canonical size >= 16")
    mask = skin_mask(frame_rgb)
    if not np.any(mask):
        raise ValueError("skin mask is empty; cannot choose comparable crops")
    height, width = mask.shape
    crop_size = min(height, width, max(48, canonical_size))
    if crop_size < 16:
        raise ValueError("frame is too small for skin crops")
    step = max(1, crop_size // 2)
    xs = sorted(set(range(0, width - crop_size + 1, step)) | {width - crop_size})
    ys = sorted(set(range(0, height - crop_size + 1, step)) | {height - crop_size})
    integral = cv2.integral(mask, sdepth=cv2.CV_64F)
    candidates = []
    for y in ys:
        for x in xs:
            skin_pixels = (
                integral[y + crop_size, x + crop_size]
                - integral[y, x + crop_size]
                - integral[y + crop_size, x]
                + integral[y, x]
            ) / 255.0
            coverage = float(skin_pixels / (crop_size * crop_size))
            if coverage > 0:
                candidates.append((coverage, x, y))
    candidates.sort(key=lambda item: (-item[0], item[2], item[1]))
    chosen: list[tuple[float, int, int]] = []
    for candidate in candidates:
        _, x, y = candidate
        if all(
            abs(x - old_x) >= crop_size // 2 or abs(y - old_y) >= crop_size // 2
            for _, old_x, old_y in chosen
        ):
            chosen.append(candidate)
            if len(chosen) == count:
                break
    if len(chosen) < count:
        for candidate in candidates:
            if candidate not in chosen:
                chosen.append(candidate)
                if len(chosen) == count:
                    break
    if len(chosen) < count:
        raise ValueError("not enough non-empty skin crops in frame")
    crops = []
    for _, x, y in chosen:
        crop = frame_rgb[y : y + crop_size, x : x + crop_size]
        crops.append(cv2.resize(crop, (canonical_size, canonical_size), interpolation=cv2.INTER_AREA))
    return crops


def _label(index: int) -> str:
    result = ""
    value = index + 1
    while value:
        value, remainder = divmod(value - 1, 26)
        result = chr(ord("A") + remainder) + result
    return result


def make_blind_sheet(
    named_crops: dict[str, list[np.ndarray]], output: Path, seed: int
) -> dict[str, str]:
    """Write a randomized contact sheet with letters, never source names."""
    if len(named_crops) < 2:
        raise ValueError("blind comparison needs at least two variants")
    row_counts = {len(crops) for crops in named_crops.values()}
    if len(row_counts) != 1 or row_counts == {0}:
        raise ValueError("all variants need the same non-zero crop count")
    entries = list(named_crops.items())
    random.Random(seed).shuffle(entries)
    labels = {_label(index): name for index, (name, _) in enumerate(entries)}
    tile_height, tile_width = entries[0][1][0].shape[:2]
    header_height, row_header, gutter = 30, 44, 4
    rows = len(entries[0][1])
    sheet = Image.new(
        "RGB",
        (
            row_header + gutter + (tile_width + gutter) * len(entries),
            header_height + gutter + (tile_height + gutter) * rows,
        ),
        (20, 20, 20),
    )
    draw = ImageDraw.Draw(sheet)
    for column, (label, (_, crops)) in enumerate(zip(labels, entries)):
        x = row_header + gutter + column * (tile_width + gutter)
        draw.text((x + 6, 8), label, fill=(255, 255, 255))
        for row, crop in enumerate(crops):
            y = header_height + gutter + row * (tile_height + gutter)
            sheet.paste(Image.fromarray(crop, mode="RGB"), (x, y))
    for row in range(rows):
        draw.text(
            (6, header_height + gutter + row * (tile_height + gutter) + 6),
            f"{row + 1:02d}",
            fill=(200, 200, 200),
        )
    output.parent.mkdir(parents=True, exist_ok=True)
    sheet.save(output)
    return labels


def flicker_guard(
    reference_fps: float,
    candidate_fps: float,
    reference_motion: float,
    candidate_motion: float,
    relative_tolerance: float = 0.35,
) -> str | None:
    """Return why flicker is invalid, or None for a supported comparison."""
    if abs(reference_fps - candidate_fps) > 0.01:
        return (
            f"fps differs: reference={reference_fps:.3f}, "
            f"candidate={candidate_fps:.3f}"
        )
    difference = abs(reference_motion - candidate_motion)
    allowed = max(0.5, relative_tolerance * max(reference_motion, candidate_motion))
    if difference > allowed:
        return (
            f"motion differs: reference delta={reference_motion:.4f}, "
            f"candidate delta={candidate_motion:.4f}"
        )
    return None


SCORE_CRITERIA = ("pore_readability", "naturalness", "wax_plastic", "overlaid_noise")


def aggregate_scores(scores: dict[str, list[dict[str, float]]]) -> dict[str, dict]:
    """Aggregate repeated blind scores; pass means good texture and few defects."""
    result: dict[str, dict] = {}
    for label, rows in scores.items():
        if not isinstance(rows, list) or not rows:
            raise ValueError(f"scores for {label} must be a non-empty list")
        aggregate: dict[str, object] = {"judges": len(rows)}
        for criterion in SCORE_CRITERIA:
            values = []
            for row in rows:
                value = row.get(criterion)
                if isinstance(value, bool) or not isinstance(value, Real) or not 1 <= value <= 10:
                    raise ValueError(f"{label}.{criterion} must contain numbers from 1 to 10")
                values.append(float(value))
            q1, median, q3 = np.percentile(values, [25, 50, 75])
            aggregate[criterion] = {
                "median": float(median),
                "q1": float(q1),
                "q3": float(q3),
                "iqr": float(q3 - q1),
            }
        aggregate["verdict"] = (
            "pass"
            if aggregate["pore_readability"]["median"] >= 6
            and aggregate["naturalness"]["median"] >= 6
            and aggregate["wax_plastic"]["median"] <= 4
            and aggregate["overlaid_noise"]["median"] <= 4
            else "fail"
        )
        result[label] = aggregate
    return result


def load_video_frames(path: Path, count: int) -> tuple[dict, list[np.ndarray]]:
    """Decode exactly N uniformly distributed RGB frames from an MP4/video."""
    if not path.is_file():
        raise ValueError(f"video not found: {path}")
    capture = cv2.VideoCapture(str(path))
    if not capture.isOpened():
        raise ValueError(f"cannot open video: {path}")
    try:
        fps = float(capture.get(cv2.CAP_PROP_FPS))
        total = int(capture.get(cv2.CAP_PROP_FRAME_COUNT))
        width = int(capture.get(cv2.CAP_PROP_FRAME_WIDTH))
        height = int(capture.get(cv2.CAP_PROP_FRAME_HEIGHT))
        if not np.isfinite(fps) or fps <= 0 or total <= 0:
            raise ValueError(f"invalid video metadata: {path}")
        if count > total:
            raise ValueError(f"requested {count} frames but {path.name} has {total}")
        indices = np.linspace(0, total - 1, num=count).round().astype(int).tolist()
        wanted = set(indices)
        decoded: dict[int, np.ndarray] = {}
        for index in range(indices[-1] + 1):
            ok, bgr = capture.read()
            if not ok:
                raise ValueError(f"failed to decode frame {index} from {path}")
            if index in wanted:
                decoded[index] = cv2.cvtColor(bgr, cv2.COLOR_BGR2RGB)
    finally:
        capture.release()
    info = {
        "path": str(path),
        "fps": fps,
        "frame_count": total,
        "width": width,
        "height": height,
        "sampled_indices": indices,
    }
    return info, [decoded[index] for index in indices]


def _motion_delta(frames: list[np.ndarray]) -> float:
    gray = [
        cv2.resize(cv2.cvtColor(frame, cv2.COLOR_RGB2GRAY), (64, 64), interpolation=cv2.INTER_AREA).astype(
            np.float32
        )
        for frame in frames
    ]
    return float(np.mean([np.abs(current - previous).mean() for previous, current in zip(gray, gray[1:])]))


def analyze_video(path: Path, frame_count: int, crop_count: int) -> tuple[dict, list[np.ndarray], list[np.ndarray]]:
    info, frames = load_video_frames(path, frame_count)
    crops = [crop for frame in frames for crop in select_skin_crops(frame, crop_count)]
    energies = [band_energy(crop.mean(axis=2).astype(np.float32), 3.0, 12.0) for crop in crops]
    info.update(
        {
            "pore_band_energy": float(np.mean(energies)),
            "skin_crops": len(crops),
            "motion_delta": _motion_delta(frames),
        }
    )
    return info, frames, crops


def _temporal_metrics(frames: list[np.ndarray], work: Path) -> dict:
    work.mkdir(parents=True, exist_ok=True)
    paths = []
    for index, frame in enumerate(frames):
        path = work / f"frame_{index:04d}.png"
        Image.fromarray(frame).save(path)
        paths.append(str(path))
    measured = texture_measure(paths, rois=[], canonical=None)
    return {
        "status": "comparable",
        "rgb_delta": measured["rgb_delta"],
        "hf_delta": measured["hf_delta"],
        "face_drift": measured["face_drift"],
    }


def write_report(report: dict, output: Path) -> None:
    output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")


def run_evaluation(
    reference: Path,
    candidates: list[Path],
    frame_count: int,
    crop_count: int,
    output_dir: Path,
    seed: int,
) -> dict:
    """Run the complete blind evaluation and write its two main artifacts."""
    if frame_count < 2:
        raise ValueError("--frames must be at least 2 for temporal metrics")
    if crop_count < 1:
        raise ValueError("--crops must be positive")
    if not candidates:
        raise ValueError("at least one candidate is required")
    output_dir.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="frames_", dir=output_dir) as temporary:
        work = Path(temporary)
        reference_info, reference_frames, reference_crops = analyze_video(reference, frame_count, crop_count)
        reference_info["flicker"] = _temporal_metrics(reference_frames, work / "reference")
        named_crops = {"reference": reference_crops}
        candidate_reports = []
        for index, candidate in enumerate(candidates):
            info, frames, crops = analyze_video(candidate, frame_count, crop_count)
            variant = f"candidate_{index}"
            named_crops[variant] = crops
            reason = flicker_guard(
                reference_info["fps"], info["fps"], reference_info["motion_delta"], info["motion_delta"]
            )
            info["flicker"] = (
                {"status": "not comparable", "reason": reason}
                if reason is not None
                else _temporal_metrics(frames, work / variant)
            )
            candidate_reports.append(info)
        labels = make_blind_sheet(named_crops, output_dir / "blind_sheet.png", seed)
    by_variant = {"reference": reference_info}
    by_variant.update({f"candidate_{index}": row for index, row in enumerate(candidate_reports)})
    report = {
        "configuration": {
            "frames": frame_count,
            "crops_per_frame": crop_count,
            "canonical_crop_size": 128,
            "pore_band_px": [3.0, 12.0],
            "seed": seed,
        },
        "reference": reference_info,
        "candidates": candidate_reports,
        "blind_key": {
            label: {"variant": variant, "path": by_variant[variant]["path"]}
            for label, variant in labels.items()
        },
    }
    write_report(report, output_dir / "report.json")
    return report


def judge_prompt(labels: list[str]) -> str:
    return f"""You are a blind vision judge. Inspect blind_sheet.png; variants are {', '.join(labels)}.
Each row is a skin crop. Do not guess source filenames or model identities.
For EACH variant, score all four criteria from 1 to 10:
- pore_readability: 1 = pores invisible, 10 = pores clearly readable;
- naturalness: 1 = artificial skin, 10 = natural skin;
- wax_plastic: 1 = no defect, 10 = maximum wax/plastic defect;
- overlaid_noise: 1 = none, 10 = maximum superimposed noise.
Judge texture, not identity or composition. Return only JSON in this form:
{{
  "{labels[0]}": [{{"pore_readability": 5, "naturalness": 5, "wax_plastic": 5, "overlaid_noise": 5}}]
}}
Include every variant. Multiple independent judges may append objects to each list."""


def print_summary(report: dict) -> None:
    reverse_key = {entry["variant"]: label for label, entry in report["blind_key"].items()}
    rows = [("reference", report["reference"])]
    rows.extend((f"candidate_{index}", row) for index, row in enumerate(report["candidates"]))
    print("\nVariant                 Blind  Pore energy    Motion delta  Flicker")
    print("-" * 112)
    for variant, row in rows:
        flicker = row["flicker"]
        if flicker["status"] == "not comparable":
            flicker_text = f"not comparable ({flicker['reason']})"
        else:
            flicker_text = (
                f"RGB={flicker['rgb_delta']:.4f} HF={flicker['hf_delta']:.4f} "
                f"face={flicker['face_drift']:.4f}"
            )
        print(
            f"{Path(row['path']).name:22.22s}  {reverse_key[variant]:5s}  "
            f"{row['pore_band_energy']:.4e}    {row['motion_delta']:10.4f}  {flicker_text}"
        )


def print_score_summary(aggregation: dict) -> None:
    print("\nBlind  Pores  Natural  Wax  Noise  Verdict")
    print("-" * 48)
    for label, row in aggregation.items():
        print(
            f"{label:5s}  {row['pore_readability']['median']:5.1f}  "
            f"{row['naturalness']['median']:7.1f}  {row['wax_plastic']['median']:3.1f}  "
            f"{row['overlaid_noise']['median']:5.1f}  {row['verdict']}"
        )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--reference", type=Path, required=True)
    parser.add_argument("--candidates", type=Path, nargs="+", required=True)
    parser.add_argument("--frames", type=int, default=6)
    parser.add_argument("--crops", type=int, default=2)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--seed", type=int, default=0)
    parser.add_argument("--dump-prompt", action="store_true")
    parser.add_argument("--scores", type=Path, help="JSON: blind label -> list of four-criterion score objects")
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        report = run_evaluation(
            args.reference, args.candidates, args.frames, args.crops, args.out, args.seed
        )
        if args.scores:
            scores = json.loads(args.scores.read_text(encoding="utf-8"))
            if not isinstance(scores, dict) or not set(scores) <= set(report["blind_key"]):
                raise ValueError("scores must be an object using only blind labels from this run")
            report["score_aggregation"] = aggregate_scores(scores)
            write_report(report, args.out / "report.json")
    except (OSError, ValueError, json.JSONDecodeError) as error:
        print(f"error: {error}", file=sys.stderr)
        return 2
    print_summary(report)
    if "score_aggregation" in report:
        print_score_summary(report["score_aggregation"])
    if args.dump_prompt:
        print("\n" + judge_prompt(list(report["blind_key"])))
    print(f"\nArtifacts: {args.out / 'report.json'} and {args.out / 'blind_sheet.png'}")
    return 0
