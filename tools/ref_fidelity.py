"""Numeric reference-vs-render fidelity checks (Pillow only, no numpy)."""

from __future__ import annotations

import argparse
import json
import math
import sys
from pathlib import Path

from PIL import Image

_MAX_EDGE = 192
_VERDICT_SAME = 0.70
_VERDICT_DRIFTED = 0.45

_SOBEL_GX = (
    (-1, 0, 1),
    (-2, 0, 2),
    (-1, 0, 1),
)
_SOBEL_GY = (
    (-1, -2, -1),
    (0, 0, 0),
    (1, 2, 1),
)


class RefFidelity:
    """Compare a product reference still to a generated render."""

    def compare(self, reference_path: str, render_path: str, out_dir: str) -> dict:
        ref_path = _require_file(reference_path, "Reference")
        render_p = _require_file(render_path, "Render")
        output = Path(out_dir)
        output.mkdir(parents=True, exist_ok=True)

        with Image.open(ref_path) as ref_img, Image.open(render_p) as rend_img:
            ref_rgb = ref_img.convert("RGB")
            rend_rgb = rend_img.convert("RGB")

        ref_gray = _downscale_gray(ref_rgb)
        rend_gray = _downscale_gray(rend_rgb)
        ref_gray = _neutralize_background(ref_gray)
        rend_gray = _neutralize_background(rend_gray)

        ref_edges = _sobel_magnitude(ref_gray)
        rend_edges = _sobel_magnitude(rend_gray)

        ref_thr = _otsu_threshold(ref_edges)
        rend_thr = _otsu_threshold(rend_edges)
        ref_bin = _dilate_bool(_binarize(ref_edges, ref_thr))
        rend_bin = _dilate_bool(_binarize(rend_edges, rend_thr))

        ref_bbox = _foreground_bbox(ref_bin)
        rend_bbox = _foreground_bbox(rend_bin)
        ref_crop = _crop_bool(ref_bin, ref_bbox)
        rend_crop = _crop_bool(rend_bin, rend_bbox)
        ref_aligned, rend_aligned = _align_crops(ref_crop, rend_crop)

        edge_iou = _binary_iou(ref_aligned, rend_aligned)
        edge_dice = _binary_dice(ref_aligned, rend_aligned)
        palette_distance = _palette_distance(ref_rgb, rend_rgb)
        bbox_ratio_match = _bbox_ratio_match(ref_bbox, rend_bbox)
        verdict = _verdict(edge_iou)

        ref_out = output / "reference.png"
        rend_out = output / "render.png"
        collage_out = output / "collage.png"
        ref_rgb.save(ref_out)
        rend_rgb.save(rend_out)
        _write_collage(ref_rgb, rend_rgb, collage_out)

        return {
            "edge_iou": round(edge_iou, 6),
            "edge_dice": round(edge_dice, 6),
            "palette_distance": round(palette_distance, 6),
            "bbox_ratio_match": round(bbox_ratio_match, 6),
            "verdict": verdict,
            "collage": str(collage_out.resolve()),
            "reference": str(ref_out.resolve()),
            "render": str(rend_out.resolve()),
        }


def _require_file(path: str, label: str) -> Path:
    resolved = Path(path)
    if not resolved.is_file():
        raise FileNotFoundError(f"{label} image not found: {path}")
    return resolved


def _downscale_gray(image: Image.Image) -> list[list[float]]:
    width, height = image.size
    scale = min(1.0, _MAX_EDGE / max(width, height))
    if scale < 1.0:
        new_w = max(1, int(round(width * scale)))
        new_h = max(1, int(round(height * scale)))
        work = image.resize((new_w, new_h), Image.Resampling.LANCZOS)
    else:
        work = image
    pixels = work.load()
    w, h = work.size
    gray: list[list[float]] = []
    for y in range(h):
        row: list[float] = []
        for x in range(w):
            r, g, b = pixels[x, y]
            row.append(0.299 * r + 0.587 * g + 0.114 * b)
        gray.append(row)
    return gray


def _neutralize_background(gray: list[list[float]]) -> list[list[float]]:
    """Flatten border-estimated backdrop so Sobel edges ignore backdrop colour."""
    height = len(gray)
    width = len(gray[0]) if height else 0
    if not height or not width:
        return gray
    border: list[float] = []
    for x in range(width):
        border.append(gray[0][x])
        border.append(gray[height - 1][x])
    for y in range(1, height - 1):
        border.append(gray[y][0])
        border.append(gray[y][width - 1])
    border.sort()
    bg_level = border[len(border) // 2]
    span = max(border) - min(border)
    values = [v for row in gray for v in row]
    full_span = max(values) - min(values)
    margin = max(6.0, full_span * 0.06, span * 0.5 + 1.0)
    return [[255.0 if abs(v - bg_level) <= margin else v for v in row] for row in gray]


def _sobel_magnitude(gray: list[list[float]]) -> list[list[float]]:
    height = len(gray)
    width = len(gray[0]) if height else 0
    out = [[0.0 for _ in range(width)] for _ in range(height)]
    for y in range(1, height - 1):
        for x in range(1, width - 1):
            gx = 0.0
            gy = 0.0
            for ky in range(3):
                for kx in range(3):
                    v = gray[y + ky - 1][x + kx - 1]
                    gx += _SOBEL_GX[ky][kx] * v
                    gy += _SOBEL_GY[ky][kx] * v
            out[y][x] = math.hypot(gx, gy)
    return out


def _otsu_threshold(values: list[list[float]]) -> float:
    flat = [v for row in values for v in row]
    if not flat:
        return 0.0
    vmax = max(flat)
    if vmax <= 0.0:
        return 0.0
    hist = [0] * 256
    for v in flat:
        idx = min(255, int(v / vmax * 255.0))
        hist[idx] += 1
    total = len(flat)
    sum_all = sum(i * hist[i] for i in range(256))
    sum_b = 0.0
    weight_b = 0
    best_var = -1.0
    best_thr = 0.0
    for i in range(256):
        weight_b += hist[i]
        if weight_b == 0:
            continue
        weight_f = total - weight_b
        if weight_f == 0:
            break
        sum_b += i * hist[i]
        mean_b = sum_b / weight_b
        mean_f = (sum_all - sum_b) / weight_f
        var_between = weight_b * weight_f * (mean_b - mean_f) ** 2
        if var_between > best_var:
            best_var = var_between
            best_thr = i / 255.0 * vmax
    return best_thr


def _binarize(values: list[list[float]], threshold: float) -> list[list[bool]]:
    return [[v >= threshold for v in row] for row in values]


def _dilate_bool(mask: list[list[bool]]) -> list[list[bool]]:
    """One-pixel dilation so edge IoU tolerates mild anti-alias shift."""
    height = len(mask)
    width = len(mask[0]) if height else 0
    out = [[False for _ in range(width)] for _ in range(height)]
    for y in range(height):
        for x in range(width):
            if not mask[y][x]:
                continue
            for dy in (-1, 0, 1):
                for dx in (-1, 0, 1):
                    ny, nx = y + dy, x + dx
                    if 0 <= ny < height and 0 <= nx < width:
                        out[ny][nx] = True
    return out


def _foreground_bbox(mask: list[list[bool]]) -> tuple[int, int, int, int]:
    height = len(mask)
    width = len(mask[0]) if height else 0
    if not height or not width:
        return (0, 0, 0, 0)
    y0, y1 = height, -1
    x0, x1 = width, -1
    for y in range(height):
        for x in range(width):
            if mask[y][x]:
                y0 = min(y0, y)
                y1 = max(y1, y)
                x0 = min(x0, x)
                x1 = max(x1, x)
    if y1 < y0:
        return (0, 0, width, height)
    pad = 1
    x0 = max(0, x0 - pad)
    y0 = max(0, y0 - pad)
    x1 = min(width - 1, x1 + pad)
    y1 = min(height - 1, y1 + pad)
    return (x0, y0, x1 + 1, y1 + 1)


def _crop_bool(mask: list[list[bool]], bbox: tuple[int, int, int, int]) -> list[list[bool]]:
    x0, y0, x1, y1 = bbox
    return [row[x0:x1] for row in mask[y0:y1]]


def _align_crops(
    a: list[list[bool]], b: list[list[bool]]
) -> tuple[list[list[bool]], list[list[bool]]]:
    h = max(len(a), len(b))
    w = max(len(a[0]) if a else 0, len(b[0]) if b else 0)
    if h == 0 or w == 0:
        return ([[False]], [[False]])
    return (_resize_bool(a, w, h), _resize_bool(b, w, h))


def _resize_bool(mask: list[list[bool]], width: int, height: int) -> list[list[bool]]:
    src_h = len(mask)
    src_w = len(mask[0]) if src_h else 0
    if src_h == 0 or src_w == 0:
        return [[False for _ in range(width)] for _ in range(height)]
    img = Image.new("L", (src_w, src_h), 0)
    px = img.load()
    for y in range(src_h):
        for x in range(src_w):
            px[x, y] = 255 if mask[y][x] else 0
    resized = img.resize((width, height), Image.Resampling.NEAREST)
    rpx = resized.load()
    return [[rpx[x, y] >= 128 for x in range(width)] for y in range(height)]


def _binary_iou(a: list[list[bool]], b: list[list[bool]]) -> float:
    inter = 0
    union = 0
    for y in range(len(a)):
        for x in range(len(a[0])):
            av, bv = a[y][x], b[y][x]
            if av or bv:
                union += 1
                if av and bv:
                    inter += 1
    return inter / union if union else 1.0


def _binary_dice(a: list[list[bool]], b: list[list[bool]]) -> float:
    inter = 0
    a_sum = 0
    b_sum = 0
    for y in range(len(a)):
        for x in range(len(a[0])):
            av, bv = a[y][x], b[y][x]
            if av:
                a_sum += 1
            if bv:
                b_sum += 1
            if av and bv:
                inter += 1
    denom = a_sum + b_sum
    return (2.0 * inter / denom) if denom else 1.0


def _bbox_ratio_match(
    a: tuple[int, int, int, int], b: tuple[int, int, int, int]
) -> float:
    aw = max(1, a[2] - a[0])
    ah = max(1, a[3] - a[1])
    bw = max(1, b[2] - b[0])
    bh = max(1, b[3] - b[1])
    ra = aw / ah
    rb = bw / bh
    denom = max(ra, rb, 1e-9)
    return max(0.0, 1.0 - abs(ra - rb) / denom)


def _rgb_to_lab(r: int, g: int, b: int) -> tuple[float, float, float]:
    def _lin(c: float) -> float:
        c /= 255.0
        if c <= 0.04045:
            return c / 12.92
        return ((c + 0.055) / 1.055) ** 2.4

    rl, gl, bl = _lin(float(r)), _lin(float(g)), _lin(float(b))
    x = rl * 0.4124564 + gl * 0.3575761 + bl * 0.1804375
    y = rl * 0.2126729 + gl * 0.7151522 + bl * 0.0721750
    z = rl * 0.0193339 + gl * 0.1191920 + bl * 0.9503041
    x /= 0.95047
    y /= 1.0
    z /= 1.08883

    def _f(t: float) -> float:
        if t > 0.008856:
            return t ** (1.0 / 3.0)
        return (7.787 * t) + (16.0 / 116.0)

    fx, fy, fz = _f(x), _f(y), _f(z)
    l = 116.0 * fy - 16.0
    a = 500.0 * (fx - fy)
    b_lab = 200.0 * (fy - fz)
    return l, a, b_lab


def _mean_lab_grid(image: Image.Image) -> list[tuple[float, float, float]]:
    w, h = image.size
    pixels = image.load()
    cells: list[tuple[float, float, float]] = []
    for gy in range(4):
        for gx in range(4):
            x0 = gx * w // 4
            x1 = (gx + 1) * w // 4
            y0 = gy * h // 4
            y1 = (gy + 1) * h // 4
            sl, sa, sb, n = 0.0, 0.0, 0.0, 0
            for y in range(y0, y1):
                for x in range(x0, x1):
                    lab = _rgb_to_lab(*pixels[x, y])
                    sl += lab[0]
                    sa += lab[1]
                    sb += lab[2]
                    n += 1
            if n:
                cells.append((sl / n, sa / n, sb / n))
            else:
                cells.append((0.0, 0.0, 0.0))
    return cells


def _palette_distance(ref: Image.Image, render: Image.Image) -> float:
    ref_cells = _mean_lab_grid(ref)
    rend_cells = _mean_lab_grid(render)
    total = 0.0
    for (l1, a1, b1), (l2, a2, b2) in zip(ref_cells, rend_cells, strict=True):
        total += math.sqrt((l1 - l2) ** 2 + (a1 - a2) ** 2 + (b1 - b2) ** 2)
    return total / len(ref_cells)


def _verdict(edge_iou: float) -> str:
    if edge_iou >= _VERDICT_SAME:
        return "same"
    if edge_iou >= _VERDICT_DRIFTED:
        return "drifted"
    return "replaced"


def _write_collage(reference: Image.Image, render: Image.Image, path: Path) -> None:
    ref, rend = reference.convert("RGB"), render.convert("RGB")
    target_h = max(ref.height, rend.height)

    def _scale_to_height(img: Image.Image) -> Image.Image:
        if img.height == target_h:
            return img
        new_w = max(1, int(round(img.width * target_h / img.height)))
        return img.resize((new_w, target_h), Image.Resampling.LANCZOS)

    ref_s = _scale_to_height(ref)
    rend_s = _scale_to_height(rend)
    gap = 8
    canvas_w = ref_s.width + gap + rend_s.width
    canvas = Image.new("RGB", (canvas_w, target_h), (255, 255, 255))
    canvas.paste(ref_s, (0, 0))
    canvas.paste(rend_s, (ref_s.width + gap, 0))
    canvas.save(path)


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Reference vs render fidelity metrics.")
    parser.add_argument("--reference", required=True, help="Path to the reference still.")
    parser.add_argument("--render", required=True, help="Path to the generated render.")
    parser.add_argument("--out", required=True, help="Output directory for collage and copies.")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    try:
        result = RefFidelity().compare(args.reference, args.render, args.out)
    except FileNotFoundError as exc:
        print(str(exc), file=sys.stderr)
        return 2
    print(json.dumps(result, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())