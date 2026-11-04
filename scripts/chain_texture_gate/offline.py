"""Offline single-chunk/chained comparison on existing videos; CPU only."""

from __future__ import annotations

import argparse
import json
import subprocess
from pathlib import Path
from typing import Sequence

from scripts import compare_video_texture
from scripts.h3_chain_pipeline_kimi import pipeline as h3
from scripts.skin_texture_metrics import materials_root, measure_frames

from .gate import evaluate_metrics, measure_added_texture


def _extract_range(video: Path, output_dir: Path, start: int, end: int) -> list[Path]:
    if start < 0 or end < start:
        raise ValueError("invalid frame range")
    output_dir.mkdir(parents=True, exist_ok=True)
    for old in output_dir.glob("frame_*.png"):
        old.unlink()
    subprocess.run(
        [
            "ffmpeg",
            "-y",
            "-v",
            "error",
            "-i",
            str(video),
            "-vf",
            f"select=between(n\\,{start}\\,{end})",
            "-fps_mode",
            "passthrough",
            str(output_dir / "frame_%05d.png"),
        ],
        check=True,
        timeout=900,
    )
    paths = sorted(output_dir.glob("frame_*.png"))
    expected = end - start + 1
    if len(paths) != expected:
        raise RuntimeError(f"expected {expected} frames from {video}, got {len(paths)}")
    return paths


def _row(label: str, paths: Sequence[Path], canonical: dict) -> dict:
    metrics = measure_frames([str(path) for path in paths], canonical=canonical)
    metrics.update(label=label, decoded_frames=len(paths))
    return metrics


def _ratio(numerator: float, denominator: float) -> float | None:
    return numerator / denominator if denominator > 1e-12 else None


def compare_existing(
    single: Path,
    assembled: Path,
    *,
    boundary: int,
    work: Path,
    frames: int = 12,
    seam_frames: int = 6,
) -> dict:
    """Compare an existing single chunk with an existing chained artifact.

    ``boundary`` is the first assembled frame contributed by the second chunk.
    The seam rows measure the end of the single chunk, the continuation after
    that boundary, and the same joined window decoded from the chained video.
    """
    if frames < 2 or seam_frames < 1:
        raise ValueError("frames must be at least 2 and seam_frames positive")
    if boundary < 1:
        raise ValueError("boundary must be positive")
    assembled_count = h3.probe_video(assembled)["frames"]
    if boundary + seam_frames > assembled_count:
        raise ValueError("boundary/seam window exceeds assembled video")

    single_samples = h3.extract_sample_frames(single, work / "single_samples", frames)
    assembled_samples = h3.extract_sample_frames(assembled, work / "assembled_samples", frames)
    canonical = compare_video_texture.canonical_sizes([str(single_samples[0])])
    rows = [
        _row("single_chunk", single_samples, canonical),
        _row("assembled", assembled_samples, canonical),
    ]

    before = h3.extract_tail_frames(single, work / "seam_before", seam_frames)
    after = _extract_range(
        assembled, work / "seam_after", boundary, boundary + seam_frames - 1
    )
    joined = _extract_range(
        assembled,
        work / "seam_window",
        boundary - seam_frames,
        boundary + seam_frames - 1,
    )
    source_window = [*before, *after]
    before_row = _row("seam_before", before, canonical)
    after_row = _row("seam_after", after, canonical)
    source_row = _row("source_seam_window", source_window, canonical)
    joined_row = _row("assembled_seam_window", joined, canonical)
    rows.extend((before_row, after_row, source_row, joined_row))

    # Texture-only seam verdict: temporal drift is deliberately not invented
    # for this offline pair, so it is zeroed solely to isolate texture rules.
    seam_metrics = measure_added_texture(before[-1], [after[0]], canonical=canonical)
    seam_metrics.update(face_drift=0.0, face_drift_tail=0.0, tail_frames=1)
    seam_gate = evaluate_metrics(seam_metrics)
    seam_texture = dict(seam_metrics)
    seam_texture.update(
        accepted=seam_gate.accepted,
        reasons=seam_gate.reasons,
        survival_floor=seam_gate.survival_floor,
        structure_floor=seam_gate.structure_floor,
        drift_measured=False,
    )

    retention = {
        "assembled_vs_single_survival": _ratio(
            float(rows[1]["survival"]), float(rows[0]["survival"])
        ),
        "assembled_vs_single_structure": _ratio(
            float(rows[1]["structure_score"]), float(rows[0]["structure_score"])
        ),
        "seam_after_vs_before_survival": _ratio(
            float(after_row["survival"]), float(before_row["survival"])
        ),
        "seam_after_vs_before_structure": _ratio(
            float(after_row["structure_score"]), float(before_row["structure_score"])
        ),
        "assembled_vs_source_seam_survival": _ratio(
            float(joined_row["survival"]), float(source_row["survival"])
        ),
        "assembled_vs_source_seam_structure": _ratio(
            float(joined_row["structure_score"]), float(source_row["structure_score"])
        ),
    }
    return {
        "single": str(single),
        "assembled": str(assembled),
        "boundary": boundary,
        "sample_frames_per_video": frames,
        "seam_frames_per_side": seam_frames,
        "canonical_roi_sizes": canonical,
        "rows": rows,
        "seam_texture": seam_texture,
        "retention": retention,
    }


def format_offline_table(report: dict) -> str:
    headers = ("label", "frames", "pore_rms", "survival", "structure", "pore")
    table_rows = []
    for row in report["rows"]:
        table_rows.append(
            [
                str(row["label"]),
                str(row["frames"]),
                f"{float(row['pore_rms']):.3f}",
                f"{float(row['survival']):.3f}",
                f"{float(row['structure_score']):.3f}",
                f"{float(row['pore']):.3e}",
            ]
        )
    widths = [len(header) for header in headers]
    for row in table_rows:
        widths = [max(width, len(cell)) for width, cell in zip(widths, row)]
    lines = [" | ".join(header.ljust(width) for header, width in zip(headers, widths))]
    lines.append("-+-".join("-" * width for width in widths))
    lines.extend(" | ".join(cell.ljust(width) for cell, width in zip(row, widths)) for row in table_rows)
    lines.append("retention:")
    for key, value in report["retention"].items():
        lines.append(f"  {key}: {value:.1%}" if value is not None else f"  {key}: undefined")
    seam = report.get("seam_texture")
    if seam:
        verdict = "ACCEPT" if seam["accepted"] else "REJECT"
        lines.append(
            f"seam added survival: {float(seam['survival']):.3f}; "
            f"texture-only gate (drift not measured): {verdict}"
        )
        if seam["reasons"]:
            lines.append("  reasons: " + "; ".join(seam["reasons"]))
    return "\n".join(lines)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--single", type=Path, required=True)
    parser.add_argument("--assembled", type=Path, required=True)
    parser.add_argument("--boundary", type=int, required=True)
    parser.add_argument("--frames", type=int, default=12)
    parser.add_argument("--seam-frames", type=int, default=6)
    parser.add_argument("--work", type=Path, default=materials_root() / "chain_texture_gate_offline")
    parser.add_argument("--json-out", type=Path)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(list(argv) if argv is not None else None)
    try:
        report = compare_existing(
            args.single,
            args.assembled,
            boundary=args.boundary,
            work=args.work,
            frames=args.frames,
            seam_frames=args.seam_frames,
        )
        if args.json_out:
            args.json_out.parent.mkdir(parents=True, exist_ok=True)
            args.json_out.write_text(
                json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
            )
    except (OSError, RuntimeError, ValueError, subprocess.CalledProcessError) as error:
        print(f"offline comparison failed: {error}")
        return 2
    print(format_offline_table(report))
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
