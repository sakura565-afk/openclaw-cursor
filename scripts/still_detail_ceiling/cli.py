"""Command-line comparison of still detail candidates."""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import sys

import cv2
import numpy as np

from scripts.still_detail_ceiling.candidates import (
    apply_kimi,
    apply_minimax,
    learned_backend_reason,
    resize_rgb,
    run_seedvr2,
)
from scripts.still_detail_ceiling.geometry import scaling_plan
from scripts.still_detail_ceiling.metrics import (
    comparison_metrics,
    measure_texture,
    oversharpening_suspected,
    pore_like_gate,
)
from scripts.still_detail_ceiling.review import (
    choose_review_roi,
    crop_at_source_roi,
    write_review_sheet,
)

_PACKAGE_DIR = Path(__file__).resolve().parent
_REPOSITORY_ROOT = _PACKAGE_DIR.parents[1]
_DEFAULT_STYLE_NAMES = (
    "qe_graded_w0.9.png",
    "final_stand_graded_crop.png",
    "qe_bathe_undress.png",
    "KATYA_UNDDRESSED.png",
)
_CANDIDATE_ORDER = (
    "bicubic",
    "lanczos",
    "real_esrgan",
    "codeformer",
    "seedvr2",
    "pore_minimax",
    "pore_kimi",
)


def _read_rgb(path: Path) -> np.ndarray:
    image = cv2.imread(str(path), cv2.IMREAD_COLOR)
    if image is None:
        raise ValueError(f"cannot read image: {path}")
    return cv2.cvtColor(image, cv2.COLOR_BGR2RGB)


def _default_images() -> list[Path]:
    scratch = Path(os.environ.get(
        "HERMES_SCRATCH_DIR",
        str(Path.home() / "AppData" / "Local" / "hermes" / "cache" / "scratch"),
    ))
    images = []
    missing = []
    for name in _DEFAULT_STYLE_NAMES:
        candidates = (_REPOSITORY_ROOT / name, scratch / name)
        source = next((path for path in candidates if path.is_file()), None)
        if source is None:
            missing.append(name)
        else:
            images.append(source)
    if missing:
        raise FileNotFoundError("default style images not found: " + ", ".join(missing))
    return images


def _parse_seedvr2_outputs(values: list[str]) -> dict[str, Path]:
    outputs = {}
    for value in values:
        style, separator, path = value.partition("=")
        if not separator or not style or not path:
            raise ValueError("--seedvr2-output expects STYLE=PATH")
        outputs[style] = Path(path)
    return outputs


def _parse_cached_outputs(values: list[str]) -> dict[tuple[str, str], Path]:
    outputs = {}
    for value in values:
        key, separator, path = value.partition("=")
        candidate, colon, style = key.partition(":")
        if not separator or not colon or candidate not in ("real_esrgan", "codeformer", "seedvr2"):
            raise ValueError("--cached-output expects CANDIDATE:STYLE=PATH")
        if not style or not path:
            raise ValueError("--cached-output expects CANDIDATE:STYLE=PATH")
        outputs[(candidate, style)] = Path(path)
    return outputs


def _parse_review_rois(values: list[str]) -> dict[str, tuple[int, int, int, int]]:
    rois = {}
    for value in values:
        style, separator, coordinates = value.partition("=")
        try:
            roi = tuple(int(item) for item in coordinates.split(","))
        except ValueError as exc:
            raise ValueError("--review-roi coordinates must be integers") from exc
        if not separator or not style or len(roi) != 4:
            raise ValueError("--review-roi expects STYLE=X0,Y0,X1,Y1")
        rois[style] = roi
    return rois


def _unavailable(candidate: str, reason: str) -> dict:
    row = {"candidate": candidate, "status": "unavailable", "reason": reason}
    if candidate in ("bicubic", "lanczos"):
        row["classification"] = "resampling_only"
    elif candidate in ("pore_minimax", "pore_kimi"):
        row["classification"] = "synthetic_texture_not_restoration"
    elif candidate == "codeformer":
        row["classification"] = "face_restoration_does_not_restore_body_pores"
    else:
        row["classification"] = "learned_detail_unmeasured"
    return row


def _evaluate_style(
    source_path: Path,
    output_dir: Path,
    target_width: int,
    review_size: int,
    aspect_tolerance: float,
    seedvr2_outputs: dict[str, Path],
    cached_outputs: dict[tuple[str, str], Path],
    review_rois: dict[str, tuple[int, int, int, int]],
    allow_seedvr2_render: bool,
) -> dict:
    source = _read_rgb(source_path)
    source_height, source_width = source.shape[:2]
    style = source_path.stem
    plan = scaling_plan(
        source_width,
        source_height,
        target_width=target_width,
        aspect_tolerance_pct=aspect_tolerance,
    )
    width, height = plan["target_width"], plan["target_height"]
    review_roi = review_rois.get(style) or choose_review_roi(source, size=review_size)
    x0, y0, x1, y1 = review_roi
    if not (0 <= x0 < x1 <= source_width and 0 <= y0 < y1 <= source_height):
        raise ValueError(f"review ROI is outside {style}: {review_roi}")
    if min(x1 - x0, y1 - y0) < 16:
        raise ValueError(f"review ROI is too small for spectral metrics: {review_roi}")
    review_roi_mode = "manual" if style in review_rois else "automatic_upper_skin_prior"
    source_crop = source[review_roi[1]:review_roi[3], review_roi[0]:review_roi[2]]

    images: dict[str, np.ndarray] = {}
    rows: dict[str, dict] = {}
    executions: dict[str, dict] = {}
    for name, method in (("bicubic", "bicubic"), ("lanczos", "lanczos")):
        images[name] = resize_rgb(source, width, height, method)
        executions[name] = {"mode": "cpu", "output_size": [width, height]}

    cached_seed = cached_outputs.get(("seedvr2", style)) or seedvr2_outputs.get(style)
    if cached_seed is not None:
        if not cached_seed.is_file():
            rows["seedvr2"] = _unavailable("seedvr2", f"cached output not found: {cached_seed}")
        else:
            cached_image = _read_rgb(cached_seed)
            cached_size = [cached_image.shape[1], cached_image.shape[0]]
            images["seedvr2"] = resize_rgb(cached_image, width, height, "canonical")
            executions["seedvr2"] = {
                "mode": "cached",
                "path": str(cached_seed),
                "cached_size": cached_size,
                "output_size": [width, height],
            }
    elif allow_seedvr2_render:
        rendered = output_dir / style / "seedvr2.png"
        rendered.parent.mkdir(parents=True, exist_ok=True)
        try:
            run_seedvr2(source_path, rendered, plan)
            images["seedvr2"] = _read_rgb(rendered)
            executions["seedvr2"] = {"mode": "rendered", "output_size": [width, height]}
        except (OSError, RuntimeError, ValueError) as exc:
            rows["seedvr2"] = _unavailable("seedvr2", str(exc))
    else:
        rows["seedvr2"] = _unavailable(
            "seedvr2",
            "unavailable in this CPU/offline run: no cached output; GPU/ComfyUI render not requested",
        )

    for candidate in ("real_esrgan", "codeformer"):
        cached = cached_outputs.get((candidate, style))
        if cached is None:
            rows[candidate] = _unavailable(candidate, learned_backend_reason(candidate))
        elif not cached.is_file():
            rows[candidate] = _unavailable(candidate, f"cached output not found: {cached}")
        else:
            cached_image = _read_rgb(cached)
            images[candidate] = resize_rgb(cached_image, width, height, "canonical")
            executions[candidate] = {
                "mode": "cached",
                "path": str(cached),
                "cached_size": [cached_image.shape[1], cached_image.shape[0]],
                "output_size": [width, height],
            }

    for candidate, function, seed in (
        ("pore_minimax", apply_minimax, 1234),
        ("pore_kimi", apply_kimi, 0),
    ):
        try:
            images[candidate] = function(images["lanczos"], seed=seed)
            executions[candidate] = {
                "mode": "cpu_still_equivalent",
                "base": "lanczos",
                "seed": seed,
                "output_size": [width, height],
            }
        except (RuntimeError, ValueError, cv2.error) as exc:
            rows[candidate] = _unavailable(candidate, f"injector failed: {exc}")

    baseline_crop = crop_at_source_roi(images["lanczos"], review_roi, source.shape[:2])
    baseline_metrics = measure_texture(baseline_crop)
    for candidate, image in images.items():
        crop = crop_at_source_roi(image, review_roi, source.shape[:2])
        metrics = measure_texture(crop)
        canonical = resize_rgb(image, source_width, source_height, "canonical")
        canonical_crop = canonical[review_roi[1]:review_roi[3], review_roi[0]:review_roi[2]]
        metrics["canonical_mae_255"] = float(np.abs(
            canonical_crop.astype(np.float32) - source_crop.astype(np.float32)
        ).mean())
        row = {
            "candidate": candidate,
            "status": "ok",
            **executions[candidate],
            "metrics": metrics,
            **comparison_metrics(metrics, baseline_metrics),
        }
        gate_accepted, gate_reason = pore_like_gate(baseline_crop, crop)
        row["pore_gate"] = {"accepted": gate_accepted, "reason": gate_reason}
        if candidate in ("bicubic", "lanczos"):
            row["classification"] = "resampling_only_cannot_raise_detail_ceiling"
        elif candidate in ("pore_minimax", "pore_kimi"):
            surviving_gain = row["surviving_pore_delta_pct_vs_lanczos"]
            if gate_accepted:
                row["classification"] = "synthetic_pores_pass_task1_gate_not_restoration"
            elif surviving_gain is not None and surviving_gain > 0.0:
                row["classification"] = "synthetic_survival_gain_but_task1_gate_rejects"
            else:
                row["classification"] = "synthetic_energy_without_survival_gain"
        elif candidate == "codeformer":
            row["classification"] = "face_restoration_not_body_pore_restoration"
        else:
            row["classification"] = "learned_detail_candidate"
        row["oversharpening_suspected"] = (
            oversharpening_suspected(row)
            if candidate not in ("pore_minimax", "pore_kimi") else False
        )
        if row["oversharpening_suspected"]:
            row["classification"] += "; sharpening_halo_suspected"
        surviving_delta = row["surviving_pore_delta_pct_vs_lanczos"]
        pore_delta = row["pore_delta_pct_vs_lanczos"]
        row["energy_without_survival"] = bool(
            pore_delta is not None and pore_delta > 5.0
            and (surviving_delta is None or surviving_delta <= 0.0)
        )
        rows[candidate] = row

    style_output = output_dir / style
    write_review_sheet(images, style_output / "review_sheet.png", review_roi, source.shape[:2])
    ordered_rows = [rows[name] for name in _CANDIDATE_ORDER]
    injectors = [
        row for row in ordered_rows
        if row["candidate"] in ("pore_minimax", "pore_kimi") and row["status"] == "ok"
    ]
    viable = [
        row for row in injectors
        if row["surviving_pore_delta_pct_vs_lanczos"] is not None
        and row["surviving_pore_delta_pct_vs_lanczos"] > 0.0
        and row["pore_gate"]["accepted"]
    ]
    if viable:
        best = max(viable, key=lambda row: row["surviving_pore_delta_pct_vs_lanczos"])
        recommendation = {
            "candidate": best["candidate"],
            "scope": "synthetic_texture_for_human_review_not_true_detail_recovery",
            "surviving_pore_delta_pct_vs_lanczos": best["surviving_pore_delta_pct_vs_lanczos"],
        }
    else:
        recommendation = {
            "candidate": "lanczos",
            "scope": "resampling_baseline_no_injector_passed_task1_pore_gate",
        }
    recommendation.update({
        "human_eye_required": True,
        "review_crop_source_xyxy": list(review_roi),
        "review_roi_mode": review_roi_mode,
        "review_sheet": str(style_output / "review_sheet.png"),
        "compare_at": "native target resolution, same crop, without fitting to window",
    })
    if min(x1 - x0, y1 - y0) < 96:
        recommendation["review_crop_warning"] = (
            "ROI is smaller than 96 px: use for artifact review only, not proof of real pores"
        )
    return {
        "style": style,
        "source": str(source_path),
        "source_size": [source_width, source_height],
        "scaling": plan,
        "review_roi_source_xyxy": list(review_roi),
        "review_roi_mode": review_roi_mode,
        "candidates": ordered_rows,
        "recommendation": recommendation,
    }


def _number(value: float | None, format_spec: str = "+.1f") -> str:
    return "—" if value is None else format(value, format_spec)


def _print_report(report: dict) -> None:
    header = (
        f"{'style':24s} {'candidate':14s} {'status':11s} {'poreΔ%':>8s} "
        f"{'survEΔ%':>8s} {'pSurvΔpp':>9s} {'HFsvΔpp':>8s} {'lapΔ%':>8s} {'MAE':>7s} flags"
    )
    print("\n" + header)
    print("-" * len(header))
    for style in report["styles"]:
        plan = style["scaling"]
        print(
            f"{style['style']}: {plan['source_width']}x{plan['source_height']} -> "
            f"{plan['target_width']}x{plan['target_height']} "
            f"(requested width {plan['requested_width']}, adjustment {plan['width_adjustment_px']:+d}), "
            f"AR rounding {plan['aspect_error_pct']:.3f}%, "
            f"violations={plan['violations'] or 'none'}"
        )
        for row in style["candidates"]:
            if row["status"] != "ok":
                print(f"{style['style']:24s} {row['candidate']:14s} {'unavailable':11s}  {row['reason']}")
                continue
            metrics = row["metrics"]
            flags = []
            if row["pore_gate"]["accepted"]:
                flags.append("PORE_GATE")
            elif "noise-like" in row["pore_gate"]["reason"]:
                flags.append("NOISE_LIKE")
            if row["oversharpening_suspected"]:
                flags.append("OVERSHARP")
            if row["energy_without_survival"]:
                flags.append("ENERGY_NO_SURVIVAL")
            print(
                f"{style['style']:24s} {row['candidate']:14s} {row['mode']:11s} "
                f"{_number(row['pore_delta_pct_vs_lanczos']):>8s} "
                f"{_number(row['surviving_pore_delta_pct_vs_lanczos']):>8s} "
                f"{_number(row['pore_survival_delta_pp_vs_lanczos']):>9s} "
                f"{_number(row['hf_survival_delta_pp_vs_lanczos']):>8s} "
                f"{_number(row['laplacian_delta_pct_vs_lanczos']):>8s} "
                f"{metrics['canonical_mae_255']:7.3f} {','.join(flags)}"
            )
        rec = style["recommendation"]
        print(
            f"  REVIEW {rec['candidate']} | source crop {rec['review_crop_source_xyxy']} | "
            f"{rec['review_sheet']}"
        )
        if "review_crop_warning" in rec:
            print(f"  WARNING {rec['review_crop_warning']}")
    print(
        "\nPore energy and survival are not proof of recovered truth. Resizers only resample; "
        "injectors synthesize texture. Final judgement remains a same-crop human eye check."
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Compare still-detail candidates without confusing energy with recovered detail."
    )
    parser.add_argument("--image", action="append", default=[], help="source PNG; repeat for styles")
    parser.add_argument("--output-dir", default=str(_PACKAGE_DIR / "results"))
    parser.add_argument("--target-width", type=int, default=1072)
    parser.add_argument("--review-size", type=int, default=256)
    parser.add_argument(
        "--review-roi", action="append", default=[], metavar="STYLE=X0,Y0,X1,Y1",
        help="manual source-pixel crop; repeat when automatic skin selection is unsuitable",
    )
    parser.add_argument("--aspect-tolerance", type=float, default=1.0)
    parser.add_argument("--seedvr2-output", action="append", default=[], metavar="STYLE=PATH")
    parser.add_argument(
        "--cached-output", action="append", default=[], metavar="CANDIDATE:STYLE=PATH",
        help="offline output for real_esrgan, codeformer or seedvr2",
    )
    parser.add_argument(
        "--run-seedvr2", action="store_true",
        help="explicitly permit the existing GPU/ComfyUI SeedVR2 adapter",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        images = [Path(value) for value in args.image] if args.image else _default_images()
        seedvr2_outputs = _parse_seedvr2_outputs(args.seedvr2_output)
        cached_outputs = _parse_cached_outputs(args.cached_output)
        review_rois = _parse_review_rois(args.review_roi)
        output_dir = Path(args.output_dir)
        output_dir.mkdir(parents=True, exist_ok=True)
        styles = [
            _evaluate_style(
                source_path=source,
                output_dir=output_dir,
                target_width=args.target_width,
                review_size=args.review_size,
                aspect_tolerance=args.aspect_tolerance,
                seedvr2_outputs=seedvr2_outputs,
                cached_outputs=cached_outputs,
                review_rois=review_rois,
                allow_seedvr2_render=args.run_seedvr2,
            )
            for source in images
        ]
    except (FileNotFoundError, OSError, RuntimeError, ValueError, cv2.error) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2

    report = {
        "schema_version": 1,
        "parameters": {
            "target_width": args.target_width,
            "alignment": 32,
            "aspect_tolerance_pct": args.aspect_tolerance,
            "review_size": args.review_size,
            "seedvr2_render_requested": args.run_seedvr2,
        },
        "metric_definitions": {
            "pore_band": "FFT energy with spatial periods 3-12 px on the fixed target-scale review ROI",
            "pore_survival": "pore energy after sigma=1.2 Gaussian blur / before blur",
            "hf_survival": "mean absolute Laplacian after sigma=1.2 Gaussian blur / before blur",
            "surviving_pore_delta": "after-blur pore energy versus Lanczos at identical target geometry",
            "task1_gate": "scripts.skin_texture_metrics.is_pore_like on Lanczos and candidate, including added-component survival",
            "canonical_mae_255": "mean absolute RGB error after resizing candidate back to source geometry",
            "comparison_rule": "all numeric rows in one style use the same normalized skin ROI and target size",
        },
        "ceiling_assessment": {
            "resizers": "cannot create detail",
            "pore_injectors": "add deterministic synthetic texture; survival gain is not recovered truth",
            "codeformer": "face restoration only; not a body-pore ceiling lifter",
            "proof_of_true_detail_recovery": False,
            "human_eye_required": True,
        },
        "styles": styles,
    }
    report_path = output_dir / "report.json"
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    _print_report(report)
    print(f"JSON: {report_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
