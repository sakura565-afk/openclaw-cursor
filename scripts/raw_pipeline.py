"""RAW camera file decoding (CR2, NEF, ARW, DNG, RAF, ORF) via rawpy.

Provides RGB uint8 arrays suitable for hashing or face pipelines. Optional JPEG
bytes helper for interoperability.
"""

from __future__ import annotations

import io
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    import numpy as np

RAW_EXTENSIONS = frozenset({
    ".cr2",
    ".nef",
    ".arw",
    ".dng",
    ".raf",
    ".orf",
})


def is_raw_path(path: Path) -> bool:
    return path.suffix.lower() in RAW_EXTENSIONS


def decode_raw_to_rgb_uint8(path: Path) -> "np.ndarray":
    """Demosaic RAW using rawpy.postprocess; returns HxWx3 RGB uint8."""
    try:
        import rawpy  # type: ignore
    except ImportError as exc:  # pragma: no cover - optional dependency
        raise RuntimeError(
            "rawpy is required for RAW images. Install with: pip install rawpy "
            "(and system libraw / build deps as documented for rawpy)."
        ) from exc

    import numpy as np

    with rawpy.imread(str(path)) as raw:
        rgb = raw.postprocess(
            use_camera_wb=True,
            no_auto_bright=False,
            output_bps=8,
            demosaic_algorithm=rawpy.DemosaicAlgorithm.LINEAR,
            half_size=False,
        )
    return np.asarray(rgb, dtype=np.uint8)


def raw_to_jpeg_bytes(path: Path, *, quality: int = 92) -> bytes:
    """Convert RAW file to JPEG bytes (RGB, typical photo defaults)."""
    from PIL import Image

    rgb = decode_raw_to_rgb_uint8(path)
    buf = io.BytesIO()
    Image.fromarray(rgb, mode="RGB").save(buf, format="JPEG", quality=quality, optimize=True)
    return buf.getvalue()
