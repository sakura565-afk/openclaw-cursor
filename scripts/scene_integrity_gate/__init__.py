"""Scene-continuity measurements and accept/reject gate for generated video."""

from .gate import GateConfig, analyze_frames, analyze_video

__all__ = ["GateConfig", "analyze_frames", "analyze_video"]
