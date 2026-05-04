#!/usr/bin/env python3
"""Utilities for preparing media files before upload."""

from __future__ import annotations

import os
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Iterable, Optional


DEFAULT_PHOTO_LIMIT_BYTES = 10 * 1024 * 1024
ResizeRunner = Callable[..., subprocess.CompletedProcess]


class ResizeError(RuntimeError):
    """Raised when an image cannot be resized to fit the target limit."""


@dataclass
class PreparedFile:
    """Represents a media file prepared for upload."""

    path: Path
    temporary: bool = False
    original_size: int = 0
    final_size: int = 0
    resized: bool = False

    def cleanup(self) -> None:
        """Remove any temporary file created during preparation."""
        if self.temporary and self.path.exists():
            self.path.unlink()


def _default_reporter(_: str) -> None:
    return None


def ensure_photo_size_under_limit(
    input_path: os.PathLike[str] | str,
    limit_bytes: int = DEFAULT_PHOTO_LIMIT_BYTES,
    runner: ResizeRunner = subprocess.run,
    reporter: Optional[Callable[[str], None]] = None,
) -> PreparedFile:
    """Return a file path suitable for Telegram photo uploads.

    If the original image already fits under ``limit_bytes``, the original file
    is returned. Otherwise, ffmpeg is used to generate progressively smaller
    JPEG versions until one fits under the requested size limit.
    """

    reporter = reporter or _default_reporter
    source = Path(input_path)
    if not source.exists():
        raise FileNotFoundError(f"Image file not found: {source}")

    original_size = source.stat().st_size
    if original_size <= limit_bytes:
        reporter(
            f"Image already within photo limit: {source.name} "
            f"({original_size} bytes)."
        )
        return PreparedFile(
            path=source,
            temporary=False,
            original_size=original_size,
            final_size=original_size,
            resized=False,
        )

    reporter(
        f"Image exceeds photo limit ({original_size} bytes); preparing a smaller copy."
    )

    presets: Iterable[tuple[int, int]] = (
        (2560, 3),
        (2048, 4),
        (1600, 5),
        (1280, 6),
        (1024, 7),
        (800, 8),
        (640, 10),
    )

    last_error: Optional[str] = None
    for max_dimension, quality in presets:
        fd, output_name = tempfile.mkstemp(prefix="telegram-photo-", suffix=".jpg")
        os.close(fd)
        output_path = Path(output_name)
        scale_filter = (
            f"scale='min({max_dimension},iw)':'min({max_dimension},ih)':"
            "force_original_aspect_ratio=decrease"
        )
        command = [
            "ffmpeg",
            "-y",
            "-loglevel",
            "error",
            "-i",
            str(source),
            "-vf",
            scale_filter,
            "-frames:v",
            "1",
            "-q:v",
            str(quality),
            str(output_path),
        ]
        reporter(
            "Trying resized variant "
            f"(max_dimension={max_dimension}, quality={quality})."
        )
        try:
            runner(command, check=True, capture_output=True, text=True)
        except subprocess.CalledProcessError as exc:
            stderr = (exc.stderr or "").strip()
            last_error = stderr or str(exc)
            if output_path.exists():
                output_path.unlink()
            continue

        candidate_size = output_path.stat().st_size
        reporter(
            f"Generated candidate {output_path.name} ({candidate_size} bytes)."
        )
        if candidate_size <= limit_bytes:
            return PreparedFile(
                path=output_path,
                temporary=True,
                original_size=original_size,
                final_size=candidate_size,
                resized=True,
            )

        output_path.unlink()

    detail = f" Last ffmpeg error: {last_error}" if last_error else ""
    raise ResizeError(
        f"Unable to resize {source} below {limit_bytes} bytes.{detail}"
    )
