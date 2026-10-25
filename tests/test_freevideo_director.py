"""Unit tests for FreeVideo director (no network)."""

from __future__ import annotations

from pathlib import Path
from unittest import mock

import pytest

from video_pipeline.config import (
    DeliveryConfig,
    DirectorConfig,
    FreeVideoConfig,
    OutputSpec,
    PipelineConfig,
)
from video_pipeline.directors.freevideo import (
    FREEVIDEO_NODE_CLASS,
    FreeVideoDirector,
    FreeVideoDirectorError,
)
from video_pipeline.runner import _get_director


def _fake_response(status: int = 200, json_data: object = None, text: str = "") -> mock.Mock:
    resp = mock.Mock()
    resp.status_code = status
    resp.text = text
    resp.json.return_value = json_data if json_data is not None else {}
    return resp


class TestFreeVideoWorkflowGraph:
    def test_build_workflow_two_nodes_and_inputs(self, tmp_path: Path) -> None:
        director = FreeVideoDirector(
            output_dir=tmp_path / "out",
            request_func=mock.Mock(),
        )
        director._upload_image = mock.Mock(return_value="chair.jpg")
        director._ensure_freevideo_installed = mock.Mock()

        source = tmp_path / "chair.png"
        source.write_bytes(b"png")
        spec = OutputSpec(format="16:9", motion="slow_push_in", duration_sec=7)

        job = director.prepare(source, spec)
        workflow = job.workflow

        assert len(workflow) == 2
        assert workflow["1"]["class_type"] == "LoadImage"
        assert workflow["1"]["inputs"]["image"] == "chair.jpg"
        assert workflow["2"]["class_type"] == FREEVIDEO_NODE_CLASS
        assert workflow["2"]["inputs"]["first"] == ["1", 0]

        fv_inputs = workflow["2"]["inputs"]
        assert "text" in fv_inputs and job.prompt_text == fv_inputs["text"]
        assert fv_inputs["width"] == 1344
        assert fv_inputs["height"] == 768
        assert fv_inputs["seconds"] == 7.29
        assert fv_inputs["seed"] == 2026090903
        assert fv_inputs["two_pass"] is True
        assert fv_inputs["base_steps"] == 8
        assert fv_inputs["refine_steps"] == 3


class TestFreeVideoNodeAvailability:
    def test_raises_when_object_info_missing_freevideo(self, tmp_path: Path) -> None:
        def request_func(method: str, url: str, **kwargs: object) -> mock.Mock:
            if url.endswith("/object_info"):
                return _fake_response(json_data={"LoadImage": {}})
            return _fake_response(status=404)

        director = FreeVideoDirector(
            output_dir=tmp_path / "out",
            comfy_url="http://127.0.0.1:8188",
            request_func=request_func,
        )
        source = tmp_path / "x.png"
        source.write_bytes(b"x")

        with pytest.raises(FreeVideoDirectorError) as exc:
            director.prepare(source, OutputSpec(format="1:1"))

        msg = str(exc.value)
        assert "FreeVideoGenerate" in msg
        assert "8188" in msg
        assert "LAB" in msg


class TestFreeVideoOutputDiscovery:
    def test_discover_newest_mp4_and_copy(self, tmp_path: Path) -> None:
        root = tmp_path / "fv_out"
        day_dir = root / "2026-10-25" / "abc"
        day_dir.mkdir(parents=True)
        old_mp4 = day_dir / "old.mp4"
        old_mp4.write_bytes(b"old")
        before = FreeVideoDirector.snapshot_mp4_files(root)

        new_mp4 = day_dir / "new.mp4"
        new_mp4.write_bytes(b"new")

        after = FreeVideoDirector.snapshot_mp4_files(root)
        picked = FreeVideoDirector.discover_newest_mp4(before, after)
        assert picked == new_mp4

        director = FreeVideoDirector(output_dir=tmp_path / "pipeline_out", request_func=mock.Mock())
        dest = tmp_path / "pipeline_out" / "clip.mp4"
        director.copy_discovered_video(picked, dest)
        assert dest.read_bytes() == b"new"


class TestFreeVideoDirectorWiring:
    def test_get_director_freevideo(self, tmp_path: Path) -> None:
        config = PipelineConfig(
            project="p",
            source_dir=tmp_path,
            outputs=[OutputSpec(format="1:1")],
            delivery=DeliveryConfig(telegram_chat=1),
            director="freevideo",
            director_config=DirectorConfig(comfy_url="http://127.0.0.1:8189"),
            output_dir=tmp_path / "out",
        )
        with mock.patch("video_pipeline.runner.FreeVideoDirector") as fv_cls:
            fv_cls.return_value = mock.Mock()
            _get_director(config)
            fv_cls.assert_called_once_with(
                output_dir=config.output_dir,
                comfy_url="http://127.0.0.1:8189",
                freevideo_config=config.freevideo,
            )

    def test_h3_remains_default_director(self, tmp_path: Path) -> None:
        config = PipelineConfig(
            project="p",
            source_dir=tmp_path,
            outputs=[OutputSpec(format="1:1")],
            delivery=DeliveryConfig(telegram_chat=1),
        )
        assert config.director == "h3"
        assert config.freevideo.width == 1344
        assert config.freevideo.output_root == Path("G:/FreeVideo_output")
