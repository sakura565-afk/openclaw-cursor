#!/usr/bin/env python
"""Blind A/B judging of video candidates.

What this tool is for
---------------------
Every skin-detail decision in this pipeline was being made on two unreliable
measurements: a "pore energy" number that cannot tell a pore from a
highlight (isotropic noise moved it by +77%), and a human score on ONE crop
(the same crop scored 5/10 and 4/10 on two passes). So this tool replaces
both with something that can be defended:

1. PAIRED crops. The crop boxes are chosen once, on the reference clip, and
   the SAME boxes are cut from every candidate. Crop 3 of A and crop 3 of B
   are therefore the same piece of skin, which is the only way a comparison
   means anything.
2. A BLIND contact sheet. The variants are shuffled with a fixed seed and
   labelled with bare letters (A, B, C). No file name reaches the deciding
   model.
3. An EXPLICIT comparability verdict. A candidate whose frame rate differs
   from the reference, or whose motion is a different take, gets
   "not comparable" plus the reason - never a flicker number that looks like
   a verdict.
4. A PROMISCUOUS score flow. The script prepares the artefacts and prints a
   ready-made prompt; the vision judge answers with JSON, which comes back
   through ``--scores`` and is aggregated as a median with an IQR, so a
   disagreement between judgements is visible instead of averaged away.

Usage
-----
    python scripts/eval_judge/judge.py \
        --reference ref.mp4 --candidates cand_a.mp4 cand_b.mp4 \
        --frames 4 --crops 3 --seed 17 --dump-prompt

    python scripts/eval_judge/judge.py ... --scores judge.json

The artefact directory defaults to ``$HERMES_SCRATCH_DIR/eval_judge``.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Sequence

import numpy as np
from PIL import Image

if __package__ in (None, ""):  # pragma: no cover - direct execution
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
    from scripts.eval_judge import metrics as metrics_mod
    from scripts.eval_judge import scoring as scoring_mod
    from scripts.eval_judge import sheet as sheet_mod
    from scripts.eval_judge import skin as skin_mod
    from scripts.eval_judge import video as video_mod
else:
    from . import metrics as metrics_mod
    from . import scoring as scoring_mod
    from . import sheet as sheet_mod
    from . import skin as skin_mod
    from . import video as video_mod

# The seed-to-seed noise floor of the pore metric, measured on two renders of
# the same scene (scripts/eval_detail_candidates.py). A difference below it
# is not a difference.
NOISE_FLOOR_PCT = 1.0

# A candidate whose pore survival ratio drops this much below the reference
# is very likely showing noise rather than pores.
SURVIVAL_DROP_RATIO = 0.9


def default_out_dir() -> Path:
    """Artefact directory: ``$HERMES_SCRATCH_DIR/eval_judge`` by default."""
    scratch = os.environ.get("HERMES_SCRATCH_DIR", "").strip()
    root = Path(scratch) if scratch else Path.home() / "AppData" / "Local" / "hermes" / "cache" / "scratch"
    return root / "eval_judge"


def parse_candidate(spec: str) -> tuple[str, str]:
    """``name=path`` or a bare path (the name is then the file stem)."""
    if "=" in spec:
        name, _, path = spec.partition("=")
        if name and path:
            return name.strip(), path.strip()
    path = spec.strip()
    return Path(path).stem or path, path


def collect_crops(
    frame_paths: Sequence[Path],
    masks: Sequence[np.ndarray],
    boxes: Sequence[Sequence[tuple[int, int, int]]],
    reference_shape: tuple[int, int],
    out_dir: Path,
    name: str,
    canonical_size: tuple[int, int],
) -> tuple[list[str], list[float], list[float], list[int]]:
    """Cut the reference boxes out of one clip's frames.

    The boxes live in reference pixels; they are scaled to this clip's
    resolution, so a candidate rendered at a different size still gets the
    same piece of skin. Every crop is resampled to the canonical size, so
    the pore band measures the same physical feature in every clip.

    ``masks`` holds the FULL-FRAME skin mask of each frame. The mask has to
    be computed on the whole frame and then sliced with the crop box: the
    background protection in the prior estimates the background from the
    frame border, so a mask computed on the crop alone treats the crop's own
    skin edge as background and erases the crop. Slicing the frame mask keeps
    the fill and the confidence honest.

    Returns the crop paths, the skin fill of every crop, the mean mask
    confidence of every crop and the index of the frame each crop came from.
    """
    reference_height, reference_width = reference_shape
    paths: list[str] = []
    fills: list[float] = []
    confidences: list[float] = []
    frame_indices: list[int] = []
    crop_dir = out_dir / "crops" / name
    crop_dir.mkdir(parents=True, exist_ok=True)

    for frame_index, (frame_path, frame_mask, frame_boxes) in enumerate(
        zip(frame_paths, masks, boxes)
    ):
        with Image.open(frame_path) as image:
            array = np.asarray(image.convert("RGB"))
        height, width = array.shape[:2]
        scale_y = height / max(1, reference_height)
        scale_x = width / max(1, reference_width)
        for box_index, (y0, x0, size) in enumerate(frame_boxes):
            scaled_y0 = int(round(y0 * scale_y))
            scaled_x0 = int(round(x0 * scale_x))
            scaled_size = max(4, int(round(size * min(scale_y, scale_x))))
            scaled_y0 = max(0, min(scaled_y0, max(0, height - 1)))
            scaled_x0 = max(0, min(scaled_x0, max(0, width - 1)))
            patch = array[
                scaled_y0:scaled_y0 + scaled_size,
                scaled_x0:scaled_x0 + scaled_size,
            ]
            if patch.size == 0:
                patch = array[:min(scaled_size, height), :min(scaled_size, width)]
            if patch.size == 0:
                continue
            crop = metrics_mod.resample_rgb(patch, canonical_size)
            crop_path = crop_dir / f"f{frame_index:02d}_c{box_index:02d}.png"
            Image.fromarray(np.ascontiguousarray(crop.astype(np.uint8))).save(crop_path)
            paths.append(str(crop_path))
            mask_patch = np.asarray(frame_mask, dtype=np.float32)[
                scaled_y0:scaled_y0 + patch.shape[0],
                scaled_x0:scaled_x0 + patch.shape[1],
            ]
            region = (0, 0, mask_patch.shape[0], mask_patch.shape[1])
            fills.append(skin_mod.crop_fill(mask_patch, region))
            confidences.append(skin_mod.crop_confidence(mask_patch, region))
            frame_indices.append(frame_index)
    return paths, fills, confidences, frame_indices


def build_prompt(
    variants: Sequence[dict],
    criteria_text: str,
    sheet_name: str,
) -> str:
    """The ready-made request for the deciding vision model.

    Nothing here may identify a variant. The crops are referred to by their
    number and by their BLIND label only - no path, because the crop files
    live in a directory named after the candidate and a path would unblind
    the whole comparison in one glance. The judge gets the contact sheet;
    the crop numbers tell it which cell is which.
    """
    lines: list[str] = []
    lines.append(
        "You are judging the SKIN TEXTURE of AI-generated video frames. "
        "Several variants of the same clip are shown. You do NOT know which "
        "variant is which, and you must not try to guess - judge only what "
        "you see."
    )
    lines.append("")
    lines.append(
        f"Contact sheet: {Path(sheet_name).name} (rows are the variants, "
        f"labelled A, B, C, ...; columns are the numbered crops)."
    )
    lines.append("")
    lines.append("CROPS (number -> variant letter):")
    for variant in variants:
        numbers = ", ".join(str(crop["number"]) for crop in variant["crops"])
        lines.append(f"  variant {variant['label']}: crops {numbers}")
    lines.append("")
    lines.append("TASK: look at every crop and score each VARIANT on four criteria.")
    lines.append(criteria_text)
    lines.append("")
    lines.append("Give every variant one number per criterion (1-10, integers).")
    lines.append("Judge what is IN the image only. Do not reward sharpness that "
                 "looks like digital noise, and do not reward a surface that is "
                 "smooth because it is waxy.")
    lines.append("")
    lines.append("Answer with JSON only, in this shape:")
    entries = ", ".join(
        f'"{variant["label"]}": {{"pore_readability": 6, "naturalness": 7, '
        f'"wax_plastic": 2, "overlay_noise": 3}}'
        for variant in variants
    )
    lines.append(f"  {{{entries}}}")
    lines.append("")
    lines.append("Afterwards, run the judge again with --scores <this file> and the "
                 "same --seed and --frames/--crops, so the scores are attached to "
                 "the right crops.")
    return "\n".join(lines)


def criteria_block() -> str:
    lines = ["  - pore_readability: readability of the pores, 1-10 (10 = the pores "
             "are clearly readable as pores, 1 = no pore structure at all);"]
    lines.append("  - naturalness: naturalness of the texture, 1-10 (10 = it looks "
                 "like real skin, 1 = it looks synthetic);")
    lines.append("  - wax_plastic: wax / plastic look, 1-10 (10 = maximum defect, "
                 "the skin looks coated or airbrushed; 1 = none);")
    lines.append("  - overlay_noise: overlay noise, 1-10 (10 = maximum, the surface "
                 "is covered by digital grain / sand; 1 = clean).")
    return "\n".join(lines)


def format_table(rows: Sequence[Sequence[str]], headers: Sequence[str]) -> str:
    widths = [len(h) for h in headers]
    for row in rows:
        for index, cell in enumerate(row):
            widths[index] = max(widths[index], len(cell))
    out = [" | ".join(h.ljust(widths[i]) for i, h in enumerate(headers))]
    out.append("-+-".join("-" * w for w in widths))
    for row in rows:
        out.append(" | ".join(cell.ljust(widths[i]) for i, cell in enumerate(row)))
    return "\n".join(out)


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Blind A/B judging of video candidates (CPU only, no GPU, no ComfyUI).",
    )
    parser.add_argument("--reference", required=True, help="the reference clip")
    parser.add_argument("--candidates", nargs="+", default=[], help="candidate clips")
    parser.add_argument("--candidate", action="append", default=[],
                        help="name=path, repeatable (overrides --candidates)")
    parser.add_argument("--frames", type=int, default=4, help="frames sampled per clip")
    parser.add_argument("--crops", type=int, default=3, help="skin crops per frame")
    parser.add_argument("--flicker-frames", type=int, default=16,
                        help="consecutive frames used for the temporal metrics")
    parser.add_argument("--crop-size", type=int, default=160, help="crop edge in pixels")
    parser.add_argument("--out", default="", help="artefact directory")
    parser.add_argument("--seed", type=int, default=17, help="shuffle seed for the blind sheet")
    parser.add_argument("--scores", default="", help="JSON with the judge's scores")
    parser.add_argument("--dump-prompt", action="store_true",
                        help="print the prompt for the deciding vision model")
    parser.add_argument("--prompt-out", default="", help="also write the prompt to this file")
    parser.add_argument("--face-roi", default="",
                        help="normalised face ROI x0,y0,x1,y1 for the drift metric")
    parser.add_argument("--min-fill", type=float, default=0.8,
                        help="minimum skin fill of a crop box (reference clip)")
    parser.add_argument("--include-reference", action=argparse.BooleanOptionalAction,
                        default=True,
                        help="put the reference into the blind sheet as one of the rows")
    parser.add_argument("--no-prompt", action="store_true",
                        help="suppress the prompt block in the console output")
    args = parser.parse_args(argv)

    reference_path = Path(args.reference)
    if not reference_path.exists():
        print(f"reference not found: {reference_path}", file=sys.stderr)
        return 2

    candidates: list[tuple[str, Path]] = []
    for spec in args.candidates:
        name, path = parse_candidate(spec)
        candidates.append((name, Path(path)))
    for spec in args.candidate:
        name, path = parse_candidate(spec)
        candidates.append((name, Path(path)))
    if not candidates:
        print("no candidates given", file=sys.stderr)
        return 2

    out_dir = Path(args.out) if args.out else default_out_dir()
    out_dir.mkdir(parents=True, exist_ok=True)
    work_dir = out_dir / "frames"

    face_roi = metrics_mod.DEFAULT_FACE_ROI
    if args.face_roi:
        face_roi = tuple(float(v) for v in args.face_roi.split(","))
        if len(face_roi) != 4:
            print("--face-roi wants x0,y0,x1,y1", file=sys.stderr)
            return 2

    reference_info = video_mod.probe(reference_path)
    duration = float(reference_info.get("duration") or 0.0)
    if duration <= 0:
        print(f"reference has no duration: {reference_path}", file=sys.stderr)
        return 2
    timestamps = video_mod.uniform_timestamps(duration, max(1, args.frames))

    # ---- the reference decides where the crops are ------------------------
    reference_frames = video_mod.extract_frames(reference_path, timestamps, work_dir / "reference")
    if not reference_frames:
        print("could not decode the reference", file=sys.stderr)
        return 2
    reference_shape = _frame_shape(reference_frames[0])

    boxes: list[list[tuple[int, int, int]]] = []
    coverages: list[float] = []
    reference_masks: list[np.ndarray] = []
    for frame_path in reference_frames:
        frame = video_mod.load_rgb(frame_path)
        mask = skin_mod.skin_mask(frame)
        reference_masks.append(mask)
        coverages.append(skin_mod.mask_coverage(mask))
        picked = skin_mod.pick_crops(mask, args.crops, args.crop_size, min_fill=args.min_fill)
        if not picked:
            picked = skin_mod.pick_crops(mask, args.crops, args.crop_size, min_fill=0.5)
        boxes.append(picked)
    crop_count = sum(len(row) for row in boxes)
    if crop_count == 0:
        print("no skin found in the reference frames - cannot cut crops", file=sys.stderr)
        return 3
    mean_coverage = float(np.mean(coverages)) if coverages else 0.0
    if mean_coverage < skin_mod.MIN_USABLE_COVERAGE:
        print(
            f"skin mask covers only {mean_coverage*100:.2f}% of the reference frames - "
            "the crops would be background, not skin",
            file=sys.stderr,
        )
        return 3
    if mean_coverage > skin_mod.SUSPICIOUS_COVERAGE:
        print(
            f"WARNING: the skin mask covers {mean_coverage*100:.1f}% of the reference "
            "frames; the crops may include background",
            file=sys.stderr,
        )

    canonical_size = (args.crop_size, args.crop_size)

    # ---- every clip is sampled at the same timestamps ---------------------
    def sample(clip: Path, tag: str) -> tuple[list[Path], list[float]]:
        info = video_mod.probe(clip)
        clip_duration = float(info.get("duration") or 0.0)
        local_timestamps = [min(t, max(0.0, clip_duration - 1e-3)) for t in timestamps]
        frames = video_mod.extract_frames(clip, local_timestamps, work_dir / tag)
        return frames, local_timestamps

    variants: list[dict] = []
    reference_flicker = _flicker_frames(reference_path, work_dir / "reference_seq",
                                        duration, args.flicker_frames)
    reference_temporal = metrics_mod.temporal_metrics(reference_flicker, face_roi)
    reference_crops, reference_fills, reference_confidence, _ = collect_crops(
        reference_frames, reference_masks, boxes, reference_shape, out_dir, "reference",
        canonical_size
    )
    reference_metrics = metrics_mod.measure_crops(reference_crops, canonical_size)
    reference_record = {
        "name": "reference",
        "path": str(reference_path),
        "info": reference_info,
        "metrics": {**reference_metrics, **reference_temporal},
        "crops": reference_crops,
        "skin_fill_mean": float(np.mean(reference_fills)) if reference_fills else 0.0,
        "skin_confidence_mean": float(np.mean(reference_confidence)) if reference_confidence else 0.0,
    }

    for name, clip_path in candidates:
        if not clip_path.exists():
            print(f"candidate not found, skipped: {name} ({clip_path})", file=sys.stderr)
            continue
        info = video_mod.probe(clip_path)
        frames, _ = sample(clip_path, name)
        if not frames:
            print(f"could not decode {name}", file=sys.stderr)
            continue
        flicker = _flicker_frames(clip_path, work_dir / f"{name}_seq", duration,
                                  args.flicker_frames)
        temporal = metrics_mod.temporal_metrics(flicker, face_roi)
        candidate_masks = [skin_mod.skin_mask(video_mod.load_rgb(path)) for path in frames]
        crops, fills, confidences, _ = collect_crops(
            frames, candidate_masks, boxes, reference_shape, out_dir, name, canonical_size
        )
        crop_metrics = metrics_mod.measure_crops(crops, canonical_size)
        correlation = _mean_correlation(reference_frames, frames)
        comparability = metrics_mod.check_comparability(
            reference_info, info, reference_temporal, temporal, correlation
        )
        # The temporal numbers are ONLY valid when the comparison holds. When
        # it does not, they are not metrics of this candidate - they are
        # numbers about a different take - so they are kept out of the
        # metrics block entirely and only the verdict travels.
        flicker_metrics = (
            {key: temporal[key] for key in ("rgb_delta", "hf_delta", "face_drift",
                                             "face_drift_max")}
            if comparability["comparable"]
            else {}
        )
        variants.append({
            "name": name,
            "path": str(clip_path),
            "info": info,
            "metrics": {**crop_metrics, **flicker_metrics},
            "flicker": comparability,
            "crops": crops,
            "skin_fill_mean": float(np.mean(fills)) if fills else 0.0,
            "skin_confidence_mean": float(np.mean(confidences)) if confidences else 0.0,
            "frame_correlation": correlation,
        })

    if not variants:
        print("no usable candidates", file=sys.stderr)
        return 3

    # ---- the blind sheet --------------------------------------------------
    sheet_entries = list(variants)
    if args.include_reference:
        sheet_entries = [reference_record] + list(variants)
    ordered_names = sheet_mod.shuffle_variants([entry["name"] for entry in sheet_entries],
                                               args.seed)
    by_name = {entry["name"]: entry for entry in sheet_entries}
    ordered = [by_name[name] for name in ordered_names]
    labels = sheet_mod.blind_labels(len(ordered))
    label_by_name = {entry["name"]: label for entry, label in zip(ordered, labels)}

    crop_number = 1
    sheet_rows: list[list[str]] = []
    prompt_variants: list[dict] = []
    for entry, label in zip(ordered, labels):
        row_paths: list[str] = []
        prompt_crops: list[dict] = []
        for crop_path in entry["crops"]:
            row_paths.append(crop_path)
            # The prompt carries the NUMBER, not the path. The path lives in
            # report.json (which the operator reads after the judgement);
            # putting it in the prompt would hand the judge the name.
            prompt_crops.append({"number": crop_number, "path": crop_path})
            crop_number += 1
        sheet_rows.append(row_paths)
        prompt_variants.append({"label": label, "crops": prompt_crops})

    sheet_path = out_dir / "blind_sheet.png"
    sheet_mod.build_sheet(
        sheet_rows, labels, sheet_path,
        title=f"blind skin A/B - {len(ordered)} variants - seed {args.seed}",
    )

    # ---- the report -------------------------------------------------------
    report: dict[str, Any] = {
        "tool": "scripts/eval_judge/judge.py",
        "created_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "seed": args.seed,
        "settings": {
            "frames": args.frames,
            "crops_per_frame": args.crops,
            "crop_size": args.crop_size,
            "flicker_frames": args.flicker_frames,
            "min_fill": args.min_fill,
            "face_roi": list(face_roi),
            "include_reference_in_sheet": bool(args.include_reference),
        },
        "reference": reference_record,
        "candidates": {entry["name"]: entry for entry in variants},
        "crop_geometry": {
            "mode": "reference_paired",
            "note": "the boxes were chosen on the reference clip and cut from every "
                    "candidate, so crop N of A and crop N of B are the same skin",
            "boxes": [[[int(y), int(x), int(s)] for (y, x, s) in row] for row in boxes],
            "crops_per_variant": crop_count,
            "skin_mask_source": skin_mod.mask_source(),
            "reference_skin_coverage": round(mean_coverage, 4),
            "reference_skin_coverage_per_frame": [round(value, 4) for value in coverages],
        },
        "blind": {
            "order": [label_by_name[entry["name"]] for entry in ordered],
            "labels": label_by_name,
            "sheet": str(sheet_path),
            "rows": [
                {"label": label, "crops": [str(p) for p in row]}
                for label, row in zip(labels, sheet_rows)
            ],
        },
        "prompt": None,
        "scores": None,
        "verdict": None,
    }

    prompt_text = build_prompt(prompt_variants, criteria_block(), str(sheet_path))
    report["prompt"] = prompt_text
    if args.prompt_out:
        Path(args.prompt_out).parent.mkdir(parents=True, exist_ok=True)
        Path(args.prompt_out).write_text(prompt_text, encoding="utf-8")

    # ---- the external judge's scores -------------------------------------
    if args.scores:
        scores = scoring_mod.load_scores(args.scores)
        # The judge may answer with the blind letters or with the file names.
        labels_by_key = dict(label_by_name)
        for entry in variants:
            labels_by_key.setdefault(entry["name"], label_by_name.get(entry["name"], entry["name"]))
        aggregated = scoring_mod.aggregate(scores, labels_by_key)
        report["scores"] = aggregated
        report["verdict"] = _final_verdict(aggregated, variants, reference_record, label_by_name)

    report_path = out_dir / "report.json"
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")

    # ---- the console ------------------------------------------------------
    _print_summary(report, reference_record, variants, label_by_name, sheet_path, report_path)

    if args.dump_prompt and not args.no_prompt:
        print()
        print("=" * 72)
        print("PROMPT FOR THE DECIDING VISION MODEL")
        print("=" * 72)
        print(prompt_text)
    return 0


def _frame_shape(path: Path) -> tuple[int, int]:
    with Image.open(path) as image:
        return image.size[1], image.size[0]


def _flicker_frames(clip: Path, work_dir: Path, duration: float, count: int) -> list[str]:
    """A run of CONSECUTIVE frames - the temporal metrics need neighbours."""
    if count <= 0:
        return []
    work_dir.mkdir(parents=True, exist_ok=True)
    for old in work_dir.glob("f_*.png"):
        old.unlink()
    start = max(0.0, duration * 0.1)
    import subprocess

    subprocess.run(
        ["ffmpeg", "-y", "-v", "error", "-ss", f"{start:.6f}", "-i", str(clip),
         "-frames:v", str(count), "-fps_mode", "passthrough",
         str(work_dir / "f_%05d.png")],
        check=False, timeout=900,
    )
    return sorted(str(path) for path in work_dir.glob("f_*.png"))


def _mean_correlation(reference_frames: Sequence[Path], candidate_frames: Sequence[Path]) -> float | None:
    if not reference_frames or not candidate_frames:
        return None
    values: list[float] = []
    for reference_path, candidate_path in zip(reference_frames, candidate_frames):
        reference = video_mod.load_rgb(reference_path)
        candidate = video_mod.load_rgb(candidate_path)
        values.append(video_mod.frame_correlation(reference, candidate))
    return float(np.mean(values)) if values else None


def _final_verdict(
    aggregated: dict,
    variants: Sequence[dict],
    reference_record: dict,
    label_by_name: dict,
) -> dict:
    """The overall verdict: the judge's answer plus what the numbers say."""
    verdict: dict[str, Any] = {
        "judge": aggregated.get("verdict", {}),
        "objective": [],
    }
    reference_pore = float(reference_record["metrics"].get("pore") or 0.0)
    reference_survival = float(reference_record["metrics"].get("survival") or 0.0)
    for entry in variants:
        label = label_by_name.get(entry["name"], entry["name"])
        metrics = entry["metrics"]
        pore = float(metrics.get("pore") or 0.0)
        delta_pct = (pore / reference_pore - 1.0) * 100.0 if reference_pore > 0 else 0.0
        survival = float(metrics.get("survival") or 0.0)
        if abs(delta_pct) <= NOISE_FLOOR_PCT:
            texture = "в шуме (разница меньше 1%)"
        elif survival < reference_survival * SURVIVAL_DROP_RATIO:
            texture = ("энергия выросла, но выживание пор упало - похоже на шум, "
                       "а не на поры")
        else:
            texture = "реальный прирост текстуры"
        flicker = entry.get("flicker", {})
        verdict["objective"].append({
            "variant": label,
            "name": entry["name"],
            "pore_delta_pct": round(delta_pct, 2),
            "significant": abs(delta_pct) > NOISE_FLOOR_PCT,
            "pore_survival": round(survival, 4),
            "reference_survival": round(reference_survival, 4),
            "texture_reading": texture,
            "flicker_status": flicker.get("status", "unknown"),
            "flicker_reason": flicker.get("reason", ""),
        })
    return verdict


def _print_summary(
    report: dict,
    reference_record: dict,
    variants: Sequence[dict],
    label_by_name: dict,
    sheet_path: Path,
    report_path: Path,
) -> None:
    reference_metrics = reference_record["metrics"]
    print()
    print(f"reference: {report['reference']['path']}")
    print(f"  {report['reference']['info']['width']}x{report['reference']['info']['height']} "
          f"{report['reference']['info']['fps']:g} fps "
          f"{report['reference']['info']['frames']} frames")
    print(f"  поры(3-12px) {reference_metrics['pore']:.4e} | выживание "
          f"{reference_metrics['survival']*100:.1f}% | RGB {reference_metrics['rgb_delta']:.4f} "
          f"| HF {reference_metrics['hf_delta']:.4f} | лицо {reference_metrics['face_drift']:.3f}")
    print()

    rows: list[list[str]] = []
    for entry in variants:
        metrics = entry["metrics"]
        reference_pore = float(reference_metrics.get("pore") or 0.0)
        pore = float(metrics.get("pore") or 0.0)
        delta = (pore / reference_pore - 1.0) * 100.0 if reference_pore > 0 else 0.0
        flicker = entry.get("flicker", {})
        comparable = bool(flicker.get("comparable", True))
        # An invalid comparison gets NO numbers, only the verdict.
        if comparable:
            rgb = f"{float(metrics.get('rgb_delta') or 0.0):.4f}"
            hf = f"{float(metrics.get('hf_delta') or 0.0):.4f}"
            face = f"{float(metrics.get('face_drift') or 0.0):.3f}"
            flicker_text = "comparable"
        else:
            rgb = hf = face = "n/a"
            flicker_text = "not comparable"
        rows.append([
            label_by_name.get(entry["name"], entry["name"]),
            entry["name"],
            f"{pore:.4e}",
            f"{delta:+6.1f}%",
            f"{float(metrics.get('survival') or 0.0)*100:.1f}%",
            f"{float(entry.get('skin_confidence_mean') or 0.0)*100:.0f}%",
            rgb,
            hf,
            face,
            flicker_text,
        ])
    print(format_table(
        rows,
        ["слеп", "вариант", "поры", "дельта", "выжив", "кожа", "RGB", "HF", "лицо", "мерцание"],
    ))
    for entry in variants:
        flicker = entry.get("flicker", {})
        if not flicker.get("comparable", True):
            print(f"  {entry['name']}: мерцание НЕ СРАВНИМО - {flicker.get('reason', '')}")

    scores = report.get("scores")
    if scores:
        print()
        print("оценки судьи:")
        for label, entry in sorted(scores["variants"].items()):
            parts = []
            for criterion, stats in entry.get("criteria", {}).items():
                parts.append(f"{criterion} {stats['median']:g} (IQR {stats['iqr']:g})")
            composite = entry.get("composite")
            suffix = f" | композит {composite:g}" if composite is not None else ""
            print(f"  {label}: " + "; ".join(parts) + suffix)
        verdict = scores.get("verdict", {})
        print(f"  вердикт: {verdict.get('status')} -> {verdict.get('winner')}"
              f" (запас {verdict.get('margin')})")
        if verdict.get("unstable_criteria"):
            print(f"  нестабильные критерии: {', '.join(verdict['unstable_criteria'])}")

    print()
    print(f"blind sheet: {sheet_path}")
    print(f"report:      {report_path}")


if __name__ == "__main__":
    raise SystemExit(main())
