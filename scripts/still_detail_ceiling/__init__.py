"""Honest CPU/offline comparison of still-image detail candidates."""

from scripts.still_detail_ceiling.geometry import scaling_plan
from scripts.still_detail_ceiling.metrics import measure_texture

__all__ = ["measure_texture", "scaling_plan"]
