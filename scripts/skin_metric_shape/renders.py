"""Recheck earlier render verdicts through the established video decoder."""

from __future__ import annotations

import json
import os
from pathlib import Path

from scripts import compare_video_texture
from scripts.skin_texture_metrics import measure_frames
from scripts.skin_texture_metrics.metrics import legacy_classify

RENDER_NAMES = (
    "CHEF_ONE_REF2VA",
    "CHEF_SMALL",
    "CHEF_REF2VA",
    "CHEF_ANAT",
    "CHEF_FIX",
    "CHEF_FIX2",
    "CHEF_FIX3",
)
CONTROL_NAMES = ("PORE_TIGHT_012", "PORE_FLOW_0.05")
# Extra renders to re-check; the report marks anything it cannot find as missing.
EXTRA_RENDER_NAMES = (
    "BOHO_MUSIC3",
    "BOHO_INSTR2",
    "BOHO_SONG_ALIGNED",
    "BOHO_SONG_GUITAR",
    "BOHO_INSTR",
    "BOHO_FACEREFINE",
)


def _scratch_root() -> Path:
    configured = os.environ.get("HERMES_SCRATCH_DIR", "").strip()
    if configured:
        return Path(configured)
    return Path.home() / "AppData" / "Local" / "hermes" / "cache" / "scratch"


def build_render_report(
    render_root: str | Path,
    *,
    work_dir: str | Path | None = None,
    frames: int = 12,
) -> dict:
    root = Path(render_root)
    work = Path(work_dir) if work_dir else _scratch_root() / "skin_metric_shape" / "renders"
    rows = []
    canonical = None
    for name in RENDER_NAMES + CONTROL_NAMES + EXTRA_RENDER_NAMES:
        path = root / f"{name}.mp4"
        if not path.exists():
            rows.append({"render": name, "status": "missing", "control": name in CONTROL_NAMES})
            continue
        decoded = compare_video_texture.extract(str(path), work / name, frames=frames)
        if not decoded:
            rows.append({"render": name, "status": "no_frames", "control": name in CONTROL_NAMES})
            continue
        if canonical is None:
            canonical = compare_video_texture.canonical_sizes(decoded)
        measured = measure_frames(decoded, canonical=canonical)
        old = legacy_classify(measured)
        new = measured["classification"]
        temporal = measured["temporal_shape"]
        rows.append({
            "render": name,
            "status": "measured",
            "control": name in CONTROL_NAMES,
            "frames": len(decoded),
            "metric_old": old,
            "metric_new": new,
            "changed": old != new,
            "old_sand_or_plastic_now_pores": old in {"digital_sand", "plastic"} and new == "pores",
            "pore_rms": measured["pore_rms"],
            "survival": measured["survival"],
            "shape_component_density_per_10k": measured["shape_component_density_per_10k"],
            "shape_median_component_width_px": measured["shape_median_component_width_px"],
            "shape_sparsity_score": measured["shape_sparsity_score"],
            "temporal_neighbor_comparisons": temporal["neighbor_comparisons"],
            "temporal_mean_highpass_correlation": temporal["mean_highpass_correlation"],
            "temporal_component_stability": temporal["temporal_component_stability"],
        })
    measured = [row for row in rows if row["status"] == "measured"]
    return {
        "tool": "scripts.skin_metric_shape.renders",
        "decoder": "scripts.compare_video_texture.extract/canonical_sizes",
        "evaluation_path": "scripts.eval_detail_candidates measure/canonical convention",
        "canonical_reference": next((row["render"] for row in measured), None),
        "summary": {
            "requested_renders": len(RENDER_NAMES),
            "measured_renders": sum(not row["control"] for row in measured),
            "missing_renders": [
                row["render"] for row in rows
                if row["status"] == "missing" and not row["control"]
            ],
            "old_sand_or_plastic_now_pores": [
                row["render"] for row in measured if row["old_sand_or_plastic_now_pores"]
            ],
            "controls_preserved": {
                row["render"]: row["metric_new"] == row["metric_old"] for row in measured if row["control"]
            },
        },
        "rows": rows,
        "interpretation_limit": (
            "Changed classes are metric reinterpretations, not new human labels. "
            "Temporal correlations are unregistered and are not used by classify()."
        ),
    }


def _markdown(report: dict) -> str:
    lines = [
        "# Earlier render verdicts rechecked with shape",
        "",
        "The same decoded pixels and canonical ROI scale are used for old and new columns.",
        "",
        "| render | old | new | pore RMS | survival | components/10k | width px | temporal corr |",
        "|---|---|---|---:|---:|---:|---:|---:|",
    ]
    for row in report["rows"]:
        if row["status"] != "measured":
            lines.append(f"| {row['render']} | {row['status']} | — | — | — | — | — | — |")
            continue
        lines.append(
            f"| {row['render']} | {row['metric_old']} | {row['metric_new']} | "
            f"{row['pore_rms']:.4f} | {row['survival']:.4f} | "
            f"{row['shape_component_density_per_10k']:.3f} | "
            f"{row['shape_median_component_width_px']:.3f} | "
            f"{row['temporal_mean_highpass_correlation']:.4f} |"
        )
    lines.extend([
        "",
        f"Old sand/plastic reinterpreted as pores: **{', '.join(report['summary']['old_sand_or_plastic_now_pores']) or 'none'}**.",
        f"Missing inputs: **{', '.join(report['summary']['missing_renders']) or 'none'}**.",
        "",
        report["interpretation_limit"],
        "",
    ])
    return "\n".join(lines)


def write_render_report(
    render_root: str | Path,
    out_dir: str | Path,
    *,
    work_dir: str | Path | None = None,
    frames: int = 12,
) -> tuple[Path, Path]:
    report = build_render_report(render_root, work_dir=work_dir, frames=frames)
    output = Path(out_dir)
    output.mkdir(parents=True, exist_ok=True)
    json_path = output / "render_recheck.json"
    markdown_path = output / "render_recheck.md"
    json_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    markdown_path.write_text(_markdown(report), encoding="utf-8")
    return json_path, markdown_path
