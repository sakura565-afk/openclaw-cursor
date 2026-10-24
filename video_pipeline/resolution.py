"""VRAM-aware H3 output resolution selection from source image aspect ratio."""

from __future__ import annotations

import argparse
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from PIL import Image

SAMPLER_NODE_ID = "7"

Orientation = Literal["landscape", "portrait", "square"]

VALID_H3_RESOLUTIONS: tuple[tuple[int, int, Orientation], ...] = (
    (704, 1024, "portrait"),
    (736, 992, "portrait"),
    (768, 512, "landscape"),
    (768, 1024, "portrait"),
    (768, 1280, "portrait"),
    (800, 1216, "portrait"),
    (832, 672, "landscape"),
    (1024, 576, "landscape"),
    (1024, 672, "landscape"),
    (1024, 768, "landscape"),
    (1024, 1024, "square"),
    (1024, 1280, "portrait"),
    (1024, 1536, "portrait"),
    (1216, 704, "landscape"),
    (1280, 768, "landscape"),
    (1280, 1024, "landscape"),
    (1280, 1280, "square"),
    (1536, 1024, "landscape"),
)


def is_h3_resolution_ok(w: int, h: int) -> bool:
    """True when H3 MiniMax can run width×height (both dimensions divisible by 32)."""
    return w % 32 == 0 and h % 32 == 0


@dataclass(frozen=True)
class Resolution:
    """Chosen output resolution and selection metadata."""

    width: int
    height: int
    ar_diff_pct: float
    source_ar: float
    pixel_budget: int
    rule: str


def pixel_budget(length_frames: int) -> int:
    """Return max output pixels (width * height) for a clip length."""
    if length_frames >= 175:
        # Pixel budget (not a round "million"): 1_048_576 = 1024×1024 so square sources fit.
        return 1_048_576
    return 1_400_000


def _relative_ar_error(candidate_ar: float, source_ar: float) -> float:
    if source_ar == 0.0:
        return float("inf")
    return abs(candidate_ar - source_ar) / source_ar


def _auto_orientation(source_ar: float) -> tuple[set[Orientation], bool]:
    """Return allowed orientation classes for source AR (landscape includes square)."""
    if source_ar >= 1.0:
        return {"landscape", "square"}, False
    return {"portrait"}, False


def _orientation_from_arg(orientation: str | None) -> Orientation | None:
    if orientation is None:
        return None
    normalized = orientation.strip().lower()
    if normalized in ("landscape", "portrait", "square"):
        return normalized  # type: ignore[return-value]
    return None


def _largest_under_budget(
    orientations: set[Orientation] | None = None,
    budget: int = 0,
) -> tuple[int, int] | None:
    best: tuple[int, int] | None = None
    best_area = -1
    for width, height, oclass in VALID_H3_RESOLUTIONS:
        if orientations is not None and oclass not in orientations:
            continue
        area = width * height
        if area > budget:
            continue
        if area > best_area:
            best_area = area
            best = (width, height)
    return best


def resolve_resolution(
    src_w: int,
    src_h: int,
    length_frames: int = 175,
    max_pixels: int | None = None,
    orientation: str | None = None,
) -> Resolution:
    """Pick H3 width/height preserving source AR within a VRAM pixel budget."""
    source_ar = src_w / src_h if src_h else 0.0
    budget = max_pixels if max_pixels is not None else pixel_budget(length_frames)

    forced = _orientation_from_arg(orientation)
    if forced is not None:
        allowed = {forced}
        source_conflict = (
            (forced == "portrait" and source_ar >= 1.0)
            or (forced == "landscape" and source_ar < 1.0)
            or (forced == "square" and source_ar != 1.0)
        )
        rule_base = "forced-orientation"
        if source_conflict:
            rule_base = "forced-orientation (source AR override)"
    else:
        allowed, _ = _auto_orientation(source_ar)
        rule_base = "auto-ar"

    candidates: list[tuple[int, int, float]] = []
    for width, height, oclass in VALID_H3_RESOLUTIONS:
        if oclass not in allowed:
            continue
        if not is_h3_resolution_ok(width, height):
            continue
        area = width * height
        if area > budget:
            continue
        cand_ar = width / height
        candidates.append((width, height, _relative_ar_error(cand_ar, source_ar)))

    if not candidates:
        picked = _largest_under_budget(allowed, budget)
        if picked is None:
            picked = _largest_under_budget(None, budget)
        if picked is None:
            picked = min(
                ((w, h) for w, h, _ in VALID_H3_RESOLUTIONS),
                key=lambda wh: wh[0] * wh[1],
            )
        width, height = picked
        cand_ar = width / height
        diff_pct = _relative_ar_error(cand_ar, source_ar) * 100.0
        return Resolution(
            width=width,
            height=height,
            ar_diff_pct=diff_pct,
            source_ar=source_ar,
            pixel_budget=budget,
            rule="fallback",
        )

    best_unbudgeted_ar = float("inf")
    for width, height, oclass in VALID_H3_RESOLUTIONS:
        if oclass not in allowed:
            continue
        cand_ar = width / height
        err = _relative_ar_error(cand_ar, source_ar)
        if err < best_unbudgeted_ar:
            best_unbudgeted_ar = err

    min_err = min(err for _, _, err in candidates)
    tied = [item for item in candidates if item[2] == min_err]
    tied.sort(key=lambda item: (item[0] * item[1], item[0]), reverse=True)
    width, height, best_err = tied[0]

    budget_clamped = best_unbudgeted_ar < min_err - 1e-12
    if forced is not None:
        rule = rule_base
    elif budget_clamped:
        rule = "auto-ar/budget-clamped"
    else:
        rule = rule_base

    return Resolution(
        width=width,
        height=height,
        ar_diff_pct=best_err * 100.0,
        source_ar=source_ar,
        pixel_budget=budget,
        rule=rule,
    )


def apply_resolution(workflow: dict, width: int, height: int) -> None:
    """Set sampler width/height on the ComfyUI workflow graph."""
    workflow[SAMPLER_NODE_ID]["inputs"]["width"] = width
    workflow[SAMPLER_NODE_ID]["inputs"]["height"] = height


def _read_image_size(image_path: Path) -> tuple[int, int]:
    with Image.open(image_path) as img:
        return img.size


def _cli(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Resolve H3 output resolution from a source image.")
    parser.add_argument("--image", required=True, type=Path, help="Source image path")
    parser.add_argument("--length", type=int, default=175, help="Clip length in frames")
    parser.add_argument("--format", default=None, help="Optional format label (e.g. 9:16); informational only")
    parser.add_argument("--max-pixels", type=int, default=None, help="Override pixel budget")
    args = parser.parse_args(argv)

    src_w, src_h = 1024, 1024
    if not args.image.exists():
        print(f"warning: image not found: {args.image}", file=sys.stderr)
    else:
        try:
            src_w, src_h = _read_image_size(args.image)
        except OSError as exc:
            print(f"warning: could not read image {args.image}: {exc}", file=sys.stderr)

    resolved = resolve_resolution(
        src_w,
        src_h,
        length_frames=args.length,
        max_pixels=args.max_pixels,
    )
    print(f"source: {src_w}x{src_h} AR={resolved.source_ar:.4f}")
    if args.format:
        print(f"format label: {args.format}")
    print(f"chosen: {resolved.width}x{resolved.height}")
    print(f"AR diff: {resolved.ar_diff_pct:.2f}%")
    print(f"rule: {resolved.rule}")
    print(f"pixel budget: {resolved.pixel_budget}")
    return 0


if __name__ == "__main__":
    raise SystemExit(_cli())
