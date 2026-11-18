"""Declarative config-driven video pipeline orchestrator for ComfyUI I2V renders."""

from __future__ import annotations

from video_pipeline.config import PipelineConfig, load_config
from video_pipeline.runner import main

__all__ = ["PipelineConfig", "load_config", "main"]
