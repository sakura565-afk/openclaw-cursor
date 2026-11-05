"""Console table for real eye-labelled clips and before/after rollback gates."""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
from pathlib import Path
from typing import Sequence

from PIL import Image

from scripts import compare_video_texture
from scripts.probe_pore_spectrum import roi_slice

from .metrics import (
    DEFAULT_ROIS,
    is_pore_like,
    materials_root,
    measure_frames,
    measure_image,
)


def format_table(rows: Sequence[dict]) -> str:
    headers = (
        "label", "class", "structure", "pore_rms", "survival",
        "shape_n/10k", "shape_width", "pore", "pore_blur", "lap",
    )
    table_rows = []
    for row in rows:
        table_rows.append([
            str(row["label"]),
            str(row["classification"]),
            f"{float(row['structure_score']):.3f}",
            f"{float(row['pore_rms']):.3f}",
            f"{float(row['survival']):.3f}",
            f"{float(row.get('shape_component_density_per_10k', 0.0)):.1f}",
            f"{float(row.get('shape_median_component_width_px', 0.0)):.2f}",
            f"{float(row['pore']):.3e}",
            f"{float(row['pore_blur']):.3e}",
            f"{float(row['lap']):.2f}",
        ])
    widths = [len(header) for header in headers]
    for row in table_rows:
        widths = [max(width, len(cell)) for width, cell in zip(widths, row)]
    lines = [" | ".join(header.ljust(width) for header, width in zip(headers, widths))]
    lines.append("-+-".join("-" * width for width in widths))
    lines.extend(" | ".join(cell.ljust(width) for cell, width in zip(row, widths)) for row in table_rows)
    return "\n".join(lines)


def _parse_rois(specs: Sequence[str]) -> dict[str, tuple[float, float, float, float]]:
    if not specs:
        return dict(DEFAULT_ROIS)
    rois = {}
    for spec in specs:
        name, separator, values = spec.partition("=")
        numbers = tuple(float(value) for value in values.split(","))
        if not separator or not name or len(numbers) != 4:
            raise ValueError(f"ROI must be name=x0,y0,x1,y1: {spec!r}")
        rois[name] = numbers
    return rois


def _parse_video(spec: str) -> tuple[str, Path]:
    label, separator, value = spec.partition("=")
    if separator and label and value:
        return label, Path(value)
    path = Path(spec)
    return path.stem, path


def _canonical_sizes(frame_paths: Sequence[str], rois: dict) -> dict[str, tuple[int, int]]:
    if rois == DEFAULT_ROIS:
        return compare_video_texture.canonical_sizes(frame_paths)
    with Image.open(frame_paths[0]) as image:
        shape = (image.size[1], image.size[0])
    sizes = {}
    for name, roi in rois.items():
        y0, y1, x0, x1 = roi_slice(shape, roi)
        sizes[name] = (y1 - y0, x1 - x0)
    return sizes


def _safe_label(label: str) -> str:
    return re.sub(r"[^A-Za-z0-9_.-]+", "_", label)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="CPU-only pore/noise/plastic metrics; no GPU and no ComfyUI.",
    )
    parser.add_argument("--real-set", action="store_true", help="measure the eye-labelled reference set")
    parser.add_argument("--video", action="append", default=[], help="video path or label=path; repeatable")
    parser.add_argument("--before", help="before image for the is_pore_like rollback gate")
    parser.add_argument("--after", help="after image for the is_pore_like rollback gate")
    parser.add_argument("--frames", type=int, default=12)
    parser.add_argument("--roi", action="append", default=[], help="name=x0,y0,x1,y1; repeatable")
    parser.add_argument("--work", help="decoded-frame directory; defaults below HERMES_SCRATCH_DIR")
    parser.add_argument("--json-out", help="optional machine-readable report")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if bool(args.before) != bool(args.after):
        print("--before and --after must be supplied together", file=sys.stderr)
        return 2
    if args.frames < 1:
        print("--frames must be positive", file=sys.stderr)
        return 2
    try:
        rois = _parse_rois(args.roi)
    except ValueError as error:
        print(str(error), file=sys.stderr)
        return 2

    root = materials_root()
    videos = [_parse_video(spec) for spec in args.video]
    pairs = []
    if args.real_set or (not videos and not args.before):
        videos = [
            ("plastic_full_v2", root / "chain_high" / "full_v2.mp4"),
            ("sand_PORE_TIGHT_012", root / "PORE_TIGHT_012.mp4"),
            ("flow_PORE_FLOW_0.05", root / "PORE_FLOW_0.05.mp4"),
        ]
        pairs = [("BC_ab_f0_10", root / "ab_f0.png", root / "BST_ab_f0_10.png")]
    elif args.before:
        pairs = [("before_after", Path(args.before), Path(args.after))]

    missing = [str(path) for _, path in videos if not path.exists()]
    missing.extend(str(path) for _, before, after in pairs for path in (before, after) if not path.exists())
    if missing:
        print("missing inputs:\n  " + "\n  ".join(missing), file=sys.stderr)
        return 2

    work = Path(args.work) if args.work else root / "skin_texture_metrics"
    work.mkdir(parents=True, exist_ok=True)
    rows = []
    report: dict = {"videos": {}, "images": {}, "gates": {}}
    canonical = None
    try:
        if videos:
            reference_label, reference_path = videos[0]
            reference_frames = compare_video_texture.extract(
                str(reference_path), work / _safe_label(reference_label), frames=args.frames
            )
            canonical = _canonical_sizes(reference_frames, rois)
            extracted = {reference_label: reference_frames}
            for label, path in videos[1:]:
                extracted[label] = compare_video_texture.extract(
                    str(path), work / _safe_label(label), frames=args.frames
                )
            for label, path in videos:
                metrics = measure_frames(extracted[label], rois=rois, canonical=canonical)
                metrics.update(label=label, source=str(path))
                rows.append(metrics)
                report["videos"][label] = metrics

        for label, before_path, after_path in pairs:
            before_metrics = measure_image(before_path, rois=rois, canonical=canonical)
            after_metrics = measure_image(after_path, rois=rois, canonical=canonical)
            for suffix, source, metrics in (
                ("before", before_path, before_metrics),
                ("after", after_path, after_metrics),
            ):
                row_label = f"{label}_{suffix}"
                metrics.update(label=row_label, source=str(source))
                rows.append(metrics)
                report["images"][row_label] = metrics
            accepted, reason = is_pore_like(before_path, after_path, rois=rois)
            report["gates"][label] = {"accepted": accepted, "reason": reason}
    except (OSError, ValueError) as error:
        print(f"measurement failed: {error}", file=sys.stderr)
        return 2

    print(format_table(rows))
    for label, gate in report["gates"].items():
        verdict = "ACCEPT" if gate["accepted"] else "REJECT"
        print(f"gate {label}: {verdict} - {gate['reason']}")
    if args.json_out:
        output = Path(args.json_out)
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"JSON: {output}")
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
