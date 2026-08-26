"""Abstract base class for video pipeline directors."""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from video_pipeline.config import OutputSpec


@dataclass
class JobSpec:
    """Prepared job specification for a single render."""

    source_image: Path
    output_spec: OutputSpec
    workflow: dict[str, Any]
    output_path: Path
    prompt_text: str
    width: int
    height: int
    length_frames: int
    extra: dict[str, Any] = field(default_factory=dict)


@dataclass
class VideoResult:
    """Result of a completed ComfyUI render."""

    video_path: Path
    prompt_id: str
    duration_sec: float
    render_sec: float
    raw_outputs: dict[str, Any] = field(default_factory=dict)


class Director(ABC):
    """Abstract director that prepares, submits, polls, and upscales video jobs."""

    @abstractmethod
    def prepare(self, source_image: Path, output_spec: OutputSpec) -> JobSpec:
        """Build a job specification from a source image and output spec.

        Args:
            source_image: Path to the input image.
            output_spec: Desired output format and motion settings.

        Returns:
            A fully prepared JobSpec ready for submission.
        """

    @abstractmethod
    def submit(self, job: JobSpec) -> str:
        """Submit a job to the render backend.

        Args:
            job: Prepared job specification.

        Returns:
            Backend prompt/job identifier.
        """

    @abstractmethod
    def poll_until_done(self, prompt_id: str, timeout_sec: int) -> VideoResult:
        """Poll until the render completes or fails.

        Args:
            prompt_id: Backend job identifier from submit().
            timeout_sec: Maximum wait time in seconds.

        Returns:
            VideoResult with path to the rendered video.
        """

    @abstractmethod
    def upscale(self, video_path: Path, target_format: str) -> Path:
        """Upscale a video to the target format dimensions via Lanczos.

        Args:
            video_path: Path to the source video.
            target_format: Aspect ratio preset key (e.g. "16:9").

        Returns:
            Path to the upscaled video (may be the same path if no upscale needed).
        """
