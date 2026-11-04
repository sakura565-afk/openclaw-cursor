"""Survival/structure acceptance for an H3 chunk and its incoming anchor."""

from __future__ import annotations

import math
import os
from dataclasses import dataclass
from typing import Mapping, Sequence

import numpy as np
from PIL import Image

from scripts.skin_texture_metrics import DEFAULT_ROIS, MIN_ADDED_SURVIVAL, measure_image

# The positive reference PORE_FLOW_0.05 scores 0.240; plastic and sand score
# 0.144 and 0.093. 0.18 keeps a 25% margin below the positive reference while
# remaining above both negative references. It is a regression threshold, not
# a universal biological constant.
MIN_STRUCTURE = 0.18
DEFAULT_MAX_SURVIVAL_DROP = 0.15
DEFAULT_MAX_STRUCTURE_DROP = 0.20


@dataclass(frozen=True)
class GateConfig:
    """Absolute noise floors plus relative retention from the first good chunk."""

    min_survival: float = MIN_ADDED_SURVIVAL
    min_structure: float = MIN_STRUCTURE
    max_survival_drop: float = DEFAULT_MAX_SURVIVAL_DROP
    max_structure_drop: float = DEFAULT_MAX_STRUCTURE_DROP
    max_face_drift: float = 8.0

    def __post_init__(self) -> None:
        values = (
            self.min_survival,
            self.min_structure,
            self.max_survival_drop,
            self.max_structure_drop,
            self.max_face_drift,
        )
        if not all(math.isfinite(value) for value in values):
            raise ValueError("gate thresholds must be finite")
        if not 0.0 <= self.min_survival <= 1.0:
            raise ValueError("minimum survival must be in [0, 1]")
        if not 0.0 <= self.min_structure <= 1.0:
            raise ValueError("minimum structure must be in [0, 1]")
        if not 0.0 <= self.max_survival_drop < 1.0:
            raise ValueError("maximum survival drop must be in [0, 1)")
        if not 0.0 <= self.max_structure_drop < 1.0:
            raise ValueError("maximum structure drop must be in [0, 1)")
        if self.max_face_drift <= 0.0:
            raise ValueError("maximum face drift must be positive")


@dataclass(frozen=True)
class TextureGateResult:
    accepted: bool
    reasons: list[str]
    penalty: float
    survival_floor: float
    structure_floor: float


def _gray(image: np.ndarray | str | os.PathLike[str]) -> np.ndarray:
    if isinstance(image, (str, os.PathLike)):
        with Image.open(image) as opened:
            array = np.asarray(opened.convert("RGB"), dtype=np.float64)
    else:
        array = np.asarray(image, dtype=np.float64)
    if array.ndim == 3 and array.shape[2] >= 3:
        array = array[..., :3].mean(axis=2)
    if array.ndim != 2 or not array.size or not np.isfinite(array).all():
        raise ValueError(f"expected a non-empty finite gray/RGB image, got {array.shape}")
    return array


def _aggregate(rows: Sequence[Mapping[str, float | str]]) -> dict[str, float]:
    """Aggregate frame measurements without averaging survival ratios."""
    if not rows:
        raise ValueError("no candidate frames to measure")
    keys = ("pore", "pore_blur", "spec", "pore_rms", "structure_score", "lap")
    result = {key: float(np.mean([float(row[key]) for row in rows])) for key in keys}
    result["pore_ratio"] = result["pore"] / max(result["spec"], 1e-9)
    result["survival"] = result["pore_blur"] / max(result["pore"], 1e-9)
    return result


def measure_added_texture(
    before: np.ndarray | str | os.PathLike[str],
    candidates: Sequence[np.ndarray | str | os.PathLike[str]],
    *,
    rois: Mapping[str, Sequence[float]] | None = None,
    canonical: Mapping[str, tuple[int, int]] | None = None,
) -> dict[str, float | int | str]:
    """Measure candidate texture and survival of ``candidate - incoming_anchor``.

    All FFT/blur work is delegated to ``skin_texture_metrics.measure_image``.
    ``survival`` is deliberately the added-component survival used by the gate;
    the finished candidate's raw blur survival is retained as
    ``texture_survival`` so a smooth underlying image cannot hide a noisy delta.
    """
    if not candidates:
        raise ValueError("no candidate frames to measure")
    selected = DEFAULT_ROIS if rois is None else rois
    before_gray = _gray(before)
    candidate_rows = []
    added_rows = []
    classifications = []
    for candidate in candidates:
        candidate_gray = _gray(candidate)
        if candidate_gray.shape != before_gray.shape:
            raise ValueError(
                f"anchor/candidate shape mismatch: {before_gray.shape} != {candidate_gray.shape}"
            )
        candidate_metrics = measure_image(candidate_gray, rois=selected, canonical=canonical)
        added_metrics = measure_image(
            candidate_gray - before_gray, rois=selected, canonical=canonical
        )
        candidate_rows.append(candidate_metrics)
        added_rows.append(added_metrics)
        classifications.append(str(candidate_metrics["classification"]))

    texture = _aggregate(candidate_rows)
    added = _aggregate(added_rows)
    result: dict[str, float | int | str] = dict(texture)
    result.update(
        texture_survival=texture["survival"],
        survival=added["survival"],
        added_pore=added["pore"],
        added_pore_blur=added["pore_blur"],
        added_structure_score=added["structure_score"],
        classification=classifications[0] if len(set(classifications)) == 1 else "mixed",
        frames=len(candidates),
    )
    return result


def _finite(value: object) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)


def evaluate_metrics(
    metrics: Mapping[str, object],
    *,
    baseline: Mapping[str, object] | None = None,
    config: GateConfig | None = None,
) -> TextureGateResult:
    """Fail closed on drift, noise-like added detail, or structural loss."""
    settings = config or GateConfig()
    reasons: list[str] = []
    penalty = 0.0

    for key, label in (("face_drift", "mean face_drift"), ("face_drift_tail", "tail face_drift")):
        value = metrics.get(key)
        if not _finite(value):
            reasons.append(f"{label} is missing or non-finite")
            penalty += 1_000_000.0
        elif float(value) > settings.max_face_drift:
            reasons.append(f"{label} {float(value):.6g} exceeds {settings.max_face_drift:.6g}")
            penalty += float(value) / settings.max_face_drift - 1.0

    tail_frames = metrics.get("tail_frames")
    if not isinstance(tail_frames, int) or isinstance(tail_frames, bool) or tail_frames < 1:
        reasons.append("tail drift has no measured tail frames")
        penalty += 1_000_000.0
    frames = metrics.get("frames")
    if not isinstance(frames, int) or isinstance(frames, bool) or frames < 1:
        reasons.append("no measured candidate frames")
        penalty += 1_000_000.0
    pore = metrics.get("pore")
    if not _finite(pore) or float(pore) <= 0.0:
        reasons.append("texture pore energy is zero, missing, or non-finite")
        penalty += 1_000_000.0

    baseline_survival = baseline.get("survival") if baseline else None
    baseline_structure = baseline.get("structure_score") if baseline else None
    survival_floor = settings.min_survival
    structure_floor = settings.min_structure
    if baseline is not None:
        if not _finite(baseline_survival) or not _finite(baseline_structure):
            reasons.append("baseline texture measurements are missing or non-finite")
            penalty += 1_000_000.0
        else:
            survival_floor = max(
                survival_floor, float(baseline_survival) * (1.0 - settings.max_survival_drop)
            )
            structure_floor = max(
                structure_floor, float(baseline_structure) * (1.0 - settings.max_structure_drop)
            )

    survival = metrics.get("survival")
    if not _finite(survival):
        reasons.append("added-band survival is missing or non-finite")
        penalty += 1_000_000.0
    elif float(survival) < survival_floor:
        baseline_note = " including baseline retention" if baseline is not None else ""
        reasons.append(
            f"added-band survival {float(survival):.3f} is below floor "
            f"{survival_floor:.3f}{baseline_note}; the added component is noise-like"
        )
        penalty += survival_floor / max(float(survival), 1e-9) - 1.0

    structure = metrics.get("structure_score")
    if not _finite(structure):
        reasons.append("structure score is missing or non-finite")
        penalty += 1_000_000.0
    elif float(structure) < structure_floor:
        baseline_note = " including baseline retention" if baseline is not None else ""
        reasons.append(
            f"structure score {float(structure):.3f} is below floor "
            f"{structure_floor:.3f}{baseline_note}"
        )
        penalty += structure_floor / max(float(structure), 1e-6) - 1.0

    return TextureGateResult(
        accepted=not reasons,
        reasons=reasons,
        penalty=penalty,
        survival_floor=survival_floor,
        structure_floor=structure_floor,
    )


def make_chunk_report(
    label: str, metrics: Mapping[str, object], result: TextureGateResult
) -> dict[str, object]:
    """Create the small, stable report contract used by tests and operators."""
    return {
        "label": label,
        "accepted": result.accepted,
        "reasons": list(result.reasons),
        "face_drift": metrics.get("face_drift"),
        "face_drift_tail": metrics.get("face_drift_tail"),
        "pore": metrics.get("pore"),
        "survival": metrics.get("survival"),
        "texture_survival": metrics.get("texture_survival"),
        "structure_score": metrics.get("structure_score"),
        "survival_floor": result.survival_floor,
        "structure_floor": result.structure_floor,
    }


def format_chunk_report(report: Mapping[str, object]) -> str:
    verdict = "ACCEPT" if report["accepted"] else "REJECT"
    reasons = report.get("reasons") or []
    lines = [f"{report['label']}: {verdict}"]
    for key in (
        "face_drift",
        "face_drift_tail",
        "pore",
        "survival",
        "texture_survival",
        "structure_score",
        "survival_floor",
        "structure_floor",
    ):
        value = report.get(key)
        lines.append(f"{key}: {float(value):.6g}" if _finite(value) else f"{key}: unavailable")
    lines.append("reasons: " + ("; ".join(str(reason) for reason in reasons) if reasons else "none"))
    return "\n".join(lines)
