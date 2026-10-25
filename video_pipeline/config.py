"""Pydantic configuration schema and YAML loader for the video pipeline."""

from __future__ import annotations

from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel, Field, ValidationError


class OutputSpec(BaseModel):
    """Single output format specification."""

    format: Literal["1:1", "9:16", "16:9", "3:4", "4:3", "2:3"]
    motion: str = "slow_push_in"
    duration_sec: int = 7
    megapixels: float = 0.5


class QualityGates(BaseModel):
    """Quality gate thresholds applied after each render."""

    max_ar_drift: float = 0.03
    max_render_sec: int = 1800
    min_resolution_h: int = 720
    expected_codec: str = "h264"
    include_banding: bool = True
    banding_max_score: float = 25.0


class DirectorConfig(BaseModel):
    """ComfyUI settings for the render director (H3 I2V).

    ``comfy_url`` is usually the LAB instance (e.g. :8189) with Wan/H3 nodes.
    This is independent of ``interpolation.comfyui_url``, which typically targets
    the PROD instance (e.g. :8188) for RIFE / VFI post-processing.
    """

    comfy_url: str = "http://127.0.0.1:8189"
    workflow_path: Path | None = None


class InterpolationConfig(BaseModel):
    """Optional RIFE frame interpolation via a dedicated ComfyUI instance.

    ``comfyui_url`` is usually the PROD instance (e.g. :8188), not the render
    director URL in ``director_config.comfy_url``.
    """

    enabled: bool = False
    target_fps: float = 48.0
    comfyui_url: str = "http://127.0.0.1:8188"


class AssemblyConfig(BaseModel):
    """Optional multi-clip concat and assembled delivery."""

    enabled: bool = False
    output_name: str = "assembled"
    deliver_per_clip: bool = True
    deliver_assembled: bool = True


class UpscaleConfig(BaseModel):
    """Post-render upscale (SeedVR2 on PROD ComfyUI, ESRGAN, or ffmpeg Lanczos)."""

    backend: Literal["auto", "seedvr2", "esrgan", "lanczos"] = "auto"
    comfyui_url: str = "http://127.0.0.1:8188"
    resolution: int = 1080
    batch_size: int = 5
    temporal_overlap: int = 3
    blocks_to_swap: int = 32


class DeliveryConfig(BaseModel):
    """Telegram delivery and notification settings."""

    telegram_chat: int
    progress_every_min: int = 30
    caption_template: str = "{name} {format} {duration}s ready"
    progress_template: str = " {project}: {done}/{all} done ({pct}%)"
    final_template: str = " {project} finished: {done}/{all}"


class RecoveryConfig(BaseModel):
    """Crash recovery and retry settings."""

    max_retries: int = 2
    retry_backoff_sec: float = 2.0
    resume_from_state: bool = True
    state_file: Path = Path("video_pipeline_state.json")


class PipelineConfig(BaseModel):
    """Top-level pipeline configuration."""

    project: str
    source_dir: Path
    outputs: list[OutputSpec]
    quality_gates: QualityGates = Field(default_factory=QualityGates)
    interpolation: InterpolationConfig = Field(default_factory=InterpolationConfig)
    upscale: UpscaleConfig = Field(default_factory=UpscaleConfig)
    assembly: AssemblyConfig = Field(default_factory=AssemblyConfig)
    delivery: DeliveryConfig
    recovery: RecoveryConfig = Field(default_factory=RecoveryConfig)
    director: str = "h3"
    director_config: DirectorConfig = Field(default_factory=DirectorConfig)
    output_dir: Path = Path("media/out/main/")


def _format_validation_error(exc: ValidationError) -> str:
    """Format a Pydantic ValidationError with field paths for YAML debugging."""
    lines = ["Configuration validation failed:"]
    for err in exc.errors():
        loc = ".".join(str(part) for part in err.get("loc", ()))
        msg = err.get("msg", "invalid value")
        lines.append(f"  - field '{loc}': {msg}")
    return "\n".join(lines)


def load_config(path: Path) -> PipelineConfig:
    """Load and validate a pipeline config from a YAML file.

    Args:
        path: Path to the YAML configuration file.

    Returns:
        Validated PipelineConfig instance.

    Raises:
        FileNotFoundError: If the config file does not exist.
        ValueError: If the YAML is invalid or fails schema validation.
    """
    if not path.exists():
        raise FileNotFoundError(f"Config file not found: {path}")

    raw = path.read_text(encoding="utf-8")
    try:
        data = yaml.safe_load(raw)
    except yaml.YAMLError as exc:
        raise ValueError(f"Invalid YAML in {path}: {exc}") from exc

    if not isinstance(data, dict):
        raise ValueError(f"Config file {path} must contain a YAML mapping at the top level.")

    try:
        return PipelineConfig.model_validate(data)
    except ValidationError as exc:
        raise ValueError(_format_validation_error(exc)) from exc


def render_template(template: str, **context: object) -> str:
    """Render a template string with ``{key}`` placeholders.

    Args:
        template: Template string with ``{key}`` placeholders.
        **context: Values to substitute.

    Returns:
        Rendered string. Unknown placeholders are left unchanged.
    """
    result = template
    for key, value in context.items():
        result = result.replace(f"{{{key}}}", str(value))
    return result
