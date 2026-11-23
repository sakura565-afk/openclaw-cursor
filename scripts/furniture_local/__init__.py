"""Локальный конвейер мебельного inpaint через ComfyUI (без облачных API)."""

from .comfy_client import ComfyUIClient, pick_workflow
from .mask import MaskBuildResult, build_mask
from .pipeline import FurniturePipelineConfig, run_pipeline
from .qa import QAReport, evaluate_acceptance

__all__ = [
    "ComfyUIClient",
    "FurniturePipelineConfig",
    "MaskBuildResult",
    "QAReport",
    "build_mask",
    "evaluate_acceptance",
    "pick_workflow",
    "run_pipeline",
]