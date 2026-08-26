"""Shared pytest fixtures for video_pipeline tests."""

from __future__ import annotations

import json
from pathlib import Path

import pytest


@pytest.fixture
def sample_config_yaml() -> str:
    """Return a valid minimal pipeline YAML string."""
    return """
project: test_project
source_dir: media/inbound/test/
outputs:
  - { format: "1:1", motion: slow_push_in, duration_sec: 7 }
  - { format: "16:9", motion: static, duration_sec: 5 }
quality_gates:
  max_ar_drift: 0.03
delivery:
  telegram_chat: 12345
  caption_template: "{name} {format} ready"
recovery:
  max_retries: 2
output_dir: media/out/test/
"""


@pytest.fixture
def sample_config_path(tmp_path: Path, sample_config_yaml: str) -> Path:
    """Write sample config to a temp file."""
    path = tmp_path / "pipeline.yaml"
    path.write_text(sample_config_yaml, encoding="utf-8")
    return path


@pytest.fixture
def temp_state_file(tmp_path: Path) -> Path:
    """Return path for a temporary state file."""
    return tmp_path / "state.json"


@pytest.fixture
def mock_ffprobe_success() -> dict:
    """Ffprobe JSON response for a valid 1280x720 h264 video."""
    return {
        "streams": [
            {
                "codec_type": "video",
                "codec_name": "h264",
                "width": 1280,
                "height": 720,
            }
        ],
        "format": {"duration": "7.0"},
    }


@pytest.fixture
def mock_ffprobe_ar_drift() -> dict:
    """Ffprobe JSON with aspect ratio drift (wide video vs square source)."""
    return {
        "streams": [
            {
                "codec_type": "video",
                "codec_name": "h264",
                "width": 1920,
                "height": 720,
            }
        ],
        "format": {"duration": "7.0"},
    }
