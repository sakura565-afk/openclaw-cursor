"""Config schema tests for video pipeline gap closures (banding, interpolation, assembly)."""

from __future__ import annotations

from pathlib import Path
from unittest import mock

import pytest

from video_pipeline.config import (
    AssemblyConfig,
    DeliveryConfig,
    DirectorConfig,
    InterpolationConfig,
    OutputSpec,
    PipelineConfig,
    QualityGates,
    load_config,
)
from video_pipeline.directors.h3 import DEFAULT_WORKFLOW_PATH
from video_pipeline.runner import _get_director


class TestQualityGatesBandingDefaults:
    def test_include_banding_defaults_true(self) -> None:
        gates = QualityGates()
        assert gates.include_banding is True
        assert gates.banding_max_score == 25.0


class TestDirectorConfigDefaults:
    def test_director_config_defaults(self) -> None:
        cfg = DirectorConfig()
        assert cfg.comfy_url == "http://127.0.0.1:8189"
        assert cfg.workflow_path is None


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
        assert config.director_config.comfy_url == "http://127.0.0.1:8189"
        assert config.director_config.workflow_path is None

    def test_pipeline_nested_defaults(self) -> None:
        config = PipelineConfig(
            project="p",
            source_dir=Path("media/"),
            outputs=[OutputSpec(format="1:1")],
            delivery=DeliveryConfig(telegram_chat=1),
        )
        assert config.interpolation.comfyui_url == "http://127.0.0.1:8188"
        assert config.assembly.output_name == "assembled"
        assert config.director_config.comfy_url == "http://127.0.0.1:8189"


class TestDefaultWorkflowPath:
    def test_default_workflow_path_is_package_absolute_and_exists(self) -> None:
        assert DEFAULT_WORKFLOW_PATH.is_absolute()
        assert DEFAULT_WORKFLOW_PATH.name == "minimax_h3_hero_short.json"
        assert DEFAULT_WORKFLOW_PATH.exists()


class TestGetDirectorWiring:
    def test_comfy_url_reaches_h3_director(self, tmp_path: Path) -> None:
        config = PipelineConfig(
            project="p",
            source_dir=tmp_path,
            outputs=[OutputSpec(format="1:1")],
            delivery=DeliveryConfig(telegram_chat=1),
            director_config=DirectorConfig(comfy_url="http://127.0.0.1:8199"),
            output_dir=tmp_path / "out",
        )
        with mock.patch("video_pipeline.runner.H3Director") as h3_cls:
            h3_cls.return_value = mock.Mock()
            _get_director(config)
            h3_cls.assert_called_once_with(
                output_dir=config.output_dir,
                comfy_url="http://127.0.0.1:8199",
            )

    def test_workflow_path_passed_when_set(self, tmp_path: Path) -> None:
        custom = tmp_path / "custom_workflow.json"
        custom.write_text("{}", encoding="utf-8")
        config = PipelineConfig(
            project="p",
            source_dir=tmp_path,
            outputs=[OutputSpec(format="1:1")],
            delivery=DeliveryConfig(telegram_chat=1),
            director_config=DirectorConfig(workflow_path=custom),
            output_dir=tmp_path / "out",
        )
        with mock.patch("video_pipeline.runner.H3Director") as h3_cls:
            h3_cls.return_value = mock.Mock()
            _get_director(config)
            h3_cls.assert_called_once_with(
                output_dir=config.output_dir,
                comfy_url="http://127.0.0.1:8189",
                workflow_path=custom,
            )
