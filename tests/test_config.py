"""Tests for video_pipeline.config."""

from __future__ import annotations

from pathlib import Path

import pytest
from pydantic import ValidationError

import yaml

from video_pipeline.config import (
    DeliveryConfig,
    OutputSpec,
    PipelineConfig,
    QualityGates,
    load_config,
    render_template,
)


class TestOutputSpec:
    def test_valid_format(self) -> None:
        spec = OutputSpec(format="16:9")
        assert spec.format == "16:9"
        assert spec.motion == "slow_push_in"
        assert spec.duration_sec == 7

    def test_invalid_format_raises(self) -> None:
        with pytest.raises(ValidationError) as exc_info:
            OutputSpec(format="21:9")
        errors = exc_info.value.errors()
        assert any("format" in str(e.get("loc")) for e in errors)


class TestPipelineConfig:
    def test_valid_yaml_parses(self, sample_config_yaml: str) -> None:
        data = yaml.safe_load(sample_config_yaml)
        config = PipelineConfig.model_validate(data)
        assert config.project == "test_project"
        assert len(config.outputs) == 2
        assert config.outputs[0].format == "1:1"
        assert config.delivery.telegram_chat == 12345

    def test_missing_required_fields_raises(self) -> None:
        with pytest.raises(ValidationError) as exc_info:
            PipelineConfig(project="x", source_dir="media/", outputs=[])
        errors = exc_info.value.errors()
        assert any("delivery" in str(e.get("loc")) for e in errors)

    def test_defaults(self) -> None:
        config = PipelineConfig(
            project="p",
            source_dir=Path("media/"),
            outputs=[OutputSpec(format="1:1")],
            delivery=DeliveryConfig(telegram_chat=1),
        )
        assert config.director == "h3"
        assert config.quality_gates.max_ar_drift == 0.03
        assert config.recovery.max_retries == 2


class TestLoadConfig:
    def test_load_from_path(self, sample_config_path: Path) -> None:
        config = load_config(sample_config_path)
        assert config.project == "test_project"
        assert config.source_dir == Path("media/inbound/test/")

    def test_missing_file_raises(self, tmp_path: Path) -> None:
        with pytest.raises(FileNotFoundError):
            load_config(tmp_path / "missing.yaml")

    def test_invalid_yaml_raises(self, tmp_path: Path) -> None:
        bad = tmp_path / "bad.yaml"
        bad.write_text("project: [unclosed", encoding="utf-8")
        with pytest.raises(ValueError, match="Invalid YAML"):
            load_config(bad)

    def test_schema_violation_points_to_field(self, tmp_path: Path) -> None:
        bad = tmp_path / "bad_schema.yaml"
        bad.write_text(
            "project: test\nsource_dir: media/\noutputs: []\ndelivery: {}\n",
            encoding="utf-8",
        )
        with pytest.raises(ValueError, match="field 'delivery.telegram_chat'"):
            load_config(bad)


class TestRenderTemplate:
    def test_replaces_placeholders(self) -> None:
        result = render_template("{name} {format} {duration}s", name="sofa", format="1:1", duration=7)
        assert result == "sofa 1:1 7s"

    def test_unknown_placeholder_left(self) -> None:
        result = render_template("{name} {unknown}", name="sofa")
        assert result == "sofa {unknown}"

    def test_delivery_template(self) -> None:
        template = "{project}: {done}/{all} done ({pct}%)"
        result = render_template(template, project="furniture", done=3, all=9, pct=33)
        assert result == "furniture: 3/9 done (33%)"
