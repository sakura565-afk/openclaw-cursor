"""Person-aware skin mask with explicit protection against warm backgrounds."""

from __future__ import annotations

import argparse
import io
import json
from pathlib import Path
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
import warnings

import numpy as np
from PIL import Image, ImageFilter

DEFAULT_COMFY_URL = "http://127.0.0.1:8188"


def _rgb_array(img: np.ndarray | Image.Image) -> np.ndarray:
    array = np.asarray(img)
    if array.ndim != 3 or array.shape[2] < 3:
        raise ValueError("expected an RGB/RGBA image")
    array = array[..., :3]
    if array.dtype != np.uint8:
        array = np.clip(array, 0, 255).astype(np.uint8)
    return array


def _smooth_range(values: np.ndarray, low: float, high: float, softness: float = 5.0) -> np.ndarray:
    values = values.astype(np.float32)
    lower = np.clip((values - (low - softness)) / softness, 0.0, 1.0)
    upper = np.clip(((high + softness) - values) / softness, 0.0, 1.0)
    return np.minimum(lower, upper)


def _remove_small_components(mask: np.ndarray, minimum: int) -> np.ndarray:
    """Remove small 8-connected components using row runs (no scipy dependency)."""
    if minimum <= 1:
        return mask
    core = mask > 0.05
    height, width = core.shape
    labels = np.full((height, width), -1, dtype=np.int32)
    parents: list[int] = []
    previous: list[tuple[int, int, int]] = []

    def find(label: int) -> int:
        while parents[label] != label:
            parents[label] = parents[parents[label]]
            label = parents[label]
        return label

    for y in range(height):
        transitions = np.flatnonzero(np.diff(np.pad(core[y].astype(np.int8), (1, 1))))
        current: list[tuple[int, int, int]] = []
        for start, end in zip(transitions[::2], transitions[1::2] - 1):
            label = len(parents)
            parents.append(label)
            for old_start, old_end, old_label in previous:
                if old_end < start - 1:
                    continue
                if old_start > end + 1:
                    break
                root, old_root = find(label), find(old_label)
                if root != old_root:
                    parents[root] = old_root
            labels[y, start : end + 1] = label
            current.append((int(start), int(end), label))
        previous = current

    if not parents:
        return mask
    roots = np.array([find(label) for label in range(len(parents))], dtype=np.int32)
    counts = np.bincount(roots)
    keep_roots = counts >= max(1, minimum)
    valid = labels >= 0
    keep = np.zeros_like(core)
    keep[valid] = keep_roots[roots[labels[valid]]]
    return mask * keep


def _comfy_json(
    base_url: str,
    path: str,
    payload: dict[str, object] | None = None,
    *,
    timeout: float,
) -> dict[str, object]:
    request = urllib.request.Request(
        base_url.rstrip("/") + path,
        data=json.dumps(payload).encode("utf-8") if payload is not None else None,
        headers={"Content-Type": "application/json"},
    )
    with urllib.request.urlopen(request, timeout=timeout) as response:
        raw = response.read().decode("utf-8", "replace")
    return json.loads(raw) if raw.strip() else {}


def _upload_comfy_image(rgb: np.ndarray, base_url: str, timeout: float) -> str:
    stream = io.BytesIO()
    Image.fromarray(rgb, mode="RGB").save(stream, format="PNG")
    filename = f"skin_mask_v2_{uuid.uuid4().hex}.png"
    boundary = uuid.uuid4().hex
    body = (
        f"--{boundary}\r\n"
        f'Content-Disposition: form-data; name="image"; filename="{filename}"\r\n'
        "Content-Type: image/png\r\n\r\n"
    ).encode("ascii") + stream.getvalue() + f"\r\n--{boundary}--\r\n".encode("ascii")
    request = urllib.request.Request(
        base_url.rstrip("/") + "/upload/image",
        data=body,
        headers={"Content-Type": f"multipart/form-data; boundary={boundary}"},
    )
    with urllib.request.urlopen(request, timeout=timeout) as response:
        result = json.loads(response.read().decode("utf-8"))
    name = result.get("name")
    if not name:
        raise RuntimeError(f"ComfyUI upload returned no filename: {result}")
    subfolder = result.get("subfolder", "")
    return f"{subfolder}/{name}" if subfolder else str(name)


def comfy_person_mask(
    img: np.ndarray | Image.Image,
    *,
    base_url: str = DEFAULT_COMFY_URL,
    timeout: float = 120.0,
    poll_interval: float = 1.0,
) -> np.ndarray:
    """Build a person prior with BC_BiRefNetRemoveBackground via ComfyUI API."""
    rgb = _rgb_array(img)
    uploaded_name = _upload_comfy_image(rgb, base_url, min(timeout, 30.0))
    graph = {
        "1": {"class_type": "LoadImage", "inputs": {"image": uploaded_name}},
        "2": {
            "class_type": "BC_BiRefNetRemoveBackground",
            "inputs": {
                "image": ["1", 0],
                "model": "BiRefNet-general",
                "sensitivity": 1.0,
                "mask_blur": 0,
                "mask_offset": 0,
                "invert_output": False,
            },
        },
        "3": {
            "class_type": "SaveImage",
            "inputs": {"images": ["2", 2], "filename_prefix": "skin_mask_v2"},
        },
    }
    response = _comfy_json(
        base_url,
        "/prompt",
        {"prompt": graph, "client_id": f"skin_mask_v2_{uuid.uuid4().hex}"},
        timeout=min(timeout, 30.0),
    )
    prompt_id = response.get("prompt_id")
    if not prompt_id:
        raise RuntimeError(f"ComfyUI returned no prompt_id: {response}")

    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        history = _comfy_json(
            base_url, f"/history/{prompt_id}", timeout=min(30.0, max(0.1, deadline - time.monotonic()))
        )
        entry = history.get(str(prompt_id))
        if isinstance(entry, dict):
            status = entry.get("status", {})
            if isinstance(status, dict) and status.get("status_str") == "error":
                raise RuntimeError(f"ComfyUI person segmentation failed: {status.get('messages', [])}")
            outputs = entry.get("outputs", {})
            images = outputs.get("3", {}).get("images", []) if isinstance(outputs, dict) else []
            if images:
                info = images[0]
                query = urllib.parse.urlencode(
                    {
                        "filename": info["filename"],
                        "subfolder": info.get("subfolder", ""),
                        "type": info.get("type", "output"),
                    }
                )
                request = urllib.request.Request(f"{base_url.rstrip('/')}/view?{query}")
                with urllib.request.urlopen(request, timeout=min(30.0, timeout)) as result:
                    mask_image = Image.open(io.BytesIO(result.read())).convert("L")
                if mask_image.size != (rgb.shape[1], rgb.shape[0]):
                    mask_image = mask_image.resize(
                        (rgb.shape[1], rgb.shape[0]), Image.Resampling.BILINEAR
                    )
                return np.asarray(mask_image, dtype=np.float32) / 255.0
        time.sleep(min(poll_interval, max(0.0, deadline - time.monotonic())))
    raise TimeoutError(f"ComfyUI person segmentation timed out after {timeout:g}s")


def skin_area_fraction(mask: np.ndarray | Image.Image) -> float:
    """Fraction of pixels whose soft mask confidence is at least 0.5."""
    array = np.asarray(mask)
    if array.size == 0:
        raise ValueError("mask must not be empty")
    return float(np.mean(array >= 0.5))


def skin_mask_v2(
    img: np.ndarray | Image.Image,
    person_mask: np.ndarray | Image.Image | None = None,
    *,
    use_comfy: bool = True,
    feather: float = 2.0,
    min_component_fraction: float = 0.0005,
    comfy_url: str = DEFAULT_COMFY_URL,
    comfy_timeout: float = 120.0,
) -> np.ndarray:
    """Return a float32 skin mask in [0, 1], optionally constrained by a person prior."""
    rgb = _rgb_array(img).astype(np.float32)
    if person_mask is None and use_comfy:
        try:
            person_mask = comfy_person_mask(
                rgb, base_url=comfy_url, timeout=comfy_timeout
            )
        except (OSError, RuntimeError, ValueError) as exc:
            warnings.warn(
                f"ComfyUI person prior unavailable ({exc}); using chromatic fallback",
                UserWarning,
                stacklevel=2,
            )
    red, green, blue = rgb[..., 0], rgb[..., 1], rgb[..., 2]
    cb = 128.0 - 0.168736 * red - 0.331264 * green + 0.5 * blue
    cr = 128.0 + 0.5 * red - 0.418688 * green - 0.081312 * blue
    mask = _smooth_range(cb, 77, 127) * _smooth_range(cr, 133, 173)

    border = max(1, min(rgb.shape[:2]) // 50)
    frame = np.concatenate(
        (cb[:border].ravel(), cb[-border:].ravel(), cb[:, :border].ravel(), cb[:, -border:].ravel())
    )
    frame_cr = np.concatenate(
        (cr[:border].ravel(), cr[-border:].ravel(), cr[:, :border].ravel(), cr[:, -border:].ravel())
    )
    distance = np.hypot(cb - np.median(frame), cr - np.median(frame_cr))
    background_protection = np.clip((distance - 10.0) / 8.0, 0.0, 1.0)
    mask *= background_protection
    if person_mask is not None:
        person = np.asarray(person_mask)
        if person.ndim == 3:
            person = person[..., 0]
        if person.shape != mask.shape:
            raise ValueError("person_mask must have the same height and width as img")
        person = person.astype(np.float32)
        if person.max(initial=0.0) > 1.0:
            person /= 255.0
        mask *= np.clip(person, 0.0, 1.0)
    minimum = int(np.ceil(min_component_fraction * mask.size))
    mask = _remove_small_components(mask, minimum)
    if feather > 0:
        quantized = Image.fromarray(np.rint(mask * 255.0).astype(np.uint8), mode="L")
        mask = np.asarray(
            quantized.filter(ImageFilter.GaussianBlur(radius=float(feather))),
            dtype=np.float32,
        ) / 255.0
    mask = np.clip(mask, 0.0, 1.0).astype(np.float32)
    fraction = skin_area_fraction(mask)
    if fraction > 0.35:
        warnings.warn(
            f"skin mask covers {fraction:.1%} of the frame (>35%); possible background capture",
            UserWarning,
            stacklevel=2,
        )
    return mask


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("image", type=Path)
    parser.add_argument("--out", type=Path, required=True, help="output grayscale mask")
    parser.add_argument("--viz", type=Path, help="optional red skin-overlay image")
    parser.add_argument("--no-comfy", action="store_true", help="force chromatic fallback")
    parser.add_argument("--feather", type=float, default=2.0)
    parser.add_argument("--min-component-fraction", type=float, default=0.0005)
    parser.add_argument("--comfy-url", default=DEFAULT_COMFY_URL)
    parser.add_argument("--comfy-timeout", type=float, default=120.0)
    args = parser.parse_args(argv)

    image = Image.open(args.image).convert("RGB")
    rgb = np.asarray(image)
    mask = skin_mask_v2(
        rgb,
        use_comfy=not args.no_comfy,
        feather=args.feather,
        min_component_fraction=args.min_component_fraction,
        comfy_url=args.comfy_url,
        comfy_timeout=args.comfy_timeout,
    )
    args.out.parent.mkdir(parents=True, exist_ok=True)
    Image.fromarray(np.rint(mask * 255.0).astype(np.uint8), mode="L").save(args.out)
    if args.viz:
        alpha = (mask * 0.45)[..., None]
        overlay = rgb.astype(np.float32) * (1.0 - alpha) + np.array(
            [255.0, 35.0, 35.0], dtype=np.float32
        ) * alpha
        args.viz.parent.mkdir(parents=True, exist_ok=True)
        Image.fromarray(np.clip(overlay, 0, 255).astype(np.uint8), mode="RGB").save(args.viz)
    print(f"skin area: {skin_area_fraction(mask):.2%}; mask: {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
