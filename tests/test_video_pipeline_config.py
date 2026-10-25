"""Config schema tests for video pipeline gap closures (banding, interpolation, assembly)."""

from __future__ import annotations

from pathlib import Path

import pytest

from video_pipeline.config import (
    AssemblyConfig,
    DeliveryConfig,
    InterpolationConfig,
    OutputSpec,
    PipelineConfig,
    QualityGates,
    load_config,
)


class TestQualityGatesBandingDefaults:
    def test_include_banding_defaults_true(self) -> None:
        gates = QualityGates()
        assert gates.include_banding is True
        assert gates.banding_max_score == 25.0


class TestInterpolationConfigDefaults:
    def test_interpolation_defaults(self) -> None:
        cfg = InterpolationConfig()
        assert cfg.enabled is False
        assert cfg.target_fps == 48.0
        assert cfg.comfyui_url == "http://127.0.0.1:8188"


class TestAssemblyConfigDefaults:
    def test_assembly_defaults(self) -> None:
        cfg = AssemblyConfig()
        assert cfg.enabled is False
        assert cfg.output_name == "assembled"
        assert cfg.deliver_per_clip is True
        assert cfg.deliver_assembled is True


class TestPipelineConfigBackwardCompatible:
    def test_old_yaml_without_new_sections(self, sample_config_path: Path) -> None:
        config = load_config(sample_config_path)
        assert config.quality_gates.include_banding is True
        assert config.interpolation.enabled is False
        assert config.assembly.enabled is False

    def test_pipeline_nested_defaults(self) -> None:
        config = PipelineConfig(
            project="p",
            source_dir=Path("media/"),
            outputs=[OutputSpec(format="1:1")],
            delivery=DeliveryConfig(telegram_chat=1),
        )
        assert config.interpolation.comfyui_url == "http://127.0.0.1:8188"
        assert config.assembly.output_name == "assembled"
