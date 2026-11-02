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
    FRAME_LOCK_CLAUSE,
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


def _comfy_upload_request(input_dir: Path) -> mock.Mock:
    """Fake ComfyUI HTTP client that persists uploads under ``input_dir``."""

    def request_func(method: str, url: str, **kwargs: object) -> mock.Mock:
        if method == "GET" and url.endswith("/object_info"):
            return _fake_response(json_data={FREEVIDEO_NODE_CLASS: {}})
        if method == "POST" and url.endswith("/upload/image"):
            files = kwargs.get("files") or {}
            image_tuple = files.get("image")
            assert image_tuple is not None
            name, handle, _mime = image_tuple
            data = handle.read()
            input_dir.mkdir(parents=True, exist_ok=True)
            (input_dir / name).write_bytes(data)
            return _fake_response(json_data={"name": name})
        return _fake_response(status=404, text=f"unexpected {method} {url}")

    return request_func


class TestFreeVideoWorkflowGraph:
    def test_build_workflow_without_last_has_no_last_input(self, tmp_path: Path) -> None:
        director = FreeVideoDirector(output_dir=tmp_path / "out", request_func=mock.Mock())
        workflow = director.build_workflow("first.jpg", "prompt text")
        assert len(workflow) == 2
        assert "last" not in workflow["2"]["inputs"]

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
        assert "last" not in fv_inputs


class TestFreeVideoLastFramePinning:
    def test_prepare_pins_last_frame_in_graph_and_uploads(self, tmp_path: Path) -> None:
        comfy_input = tmp_path / "comfy_input"
        director = FreeVideoDirector(
            output_dir=tmp_path / "out",
            request_func=_comfy_upload_request(comfy_input),
            freevideo_config=FreeVideoConfig(
                last_frame=tmp_path / "back.jpg",
            ),
        )

        first = tmp_path / "front.png"
        first.write_bytes(b"front-bytes")
        last = tmp_path / "back.jpg"
        last.write_bytes(b"back-bytes")
        spec = OutputSpec(format="16:9", motion="slow_push_in", duration_sec=7)

        job = director.prepare(first, spec)
        workflow = job.workflow

        assert len(workflow) == 3
        assert workflow["3"]["class_type"] == "LoadImage"
        last_name = workflow["3"]["inputs"]["image"]
        assert workflow["2"]["inputs"]["last"] == ["3", 0]
        assert job.extra["last_frame"] == last_name
        assert (comfy_input / last_name).read_bytes() == b"back-bytes"

    def test_missing_last_frame_raises_with_path(self, tmp_path: Path) -> None:
        missing = tmp_path / "does-not-exist.jpg"
        director = FreeVideoDirector(
            output_dir=tmp_path / "out",
            request_func=_comfy_upload_request(tmp_path / "in"),
            freevideo_config=FreeVideoConfig(last_frame=missing),
        )
        director._ensure_freevideo_installed = mock.Mock()
        director._upload_image = mock.Mock(return_value="front.jpg")

        source = tmp_path / "front.png"
        source.write_bytes(b"x")

        with pytest.raises(FreeVideoDirectorError) as exc:
            director.prepare(source, OutputSpec(format="1:1"))

        assert str(missing) in str(exc.value)


class TestFreeVideoFrameLock:
    def test_frame_lock_appends_clause_when_enabled(self, tmp_path: Path) -> None:
        director = FreeVideoDirector(
            output_dir=tmp_path / "out",
            request_func=mock.Mock(),
            freevideo_config=FreeVideoConfig(frame_lock=True),
        )
        spec = OutputSpec(format="1:1", motion="slow_push_in", duration_sec=7)
        prompt = director._build_prompt(spec)
        assert FRAME_LOCK_CLAUSE in prompt

    def test_frame_lock_disabled_leaves_prompt_unchanged(self, tmp_path: Path) -> None:
        director = FreeVideoDirector(
            output_dir=tmp_path / "out",
            request_func=mock.Mock(),
            freevideo_config=FreeVideoConfig(frame_lock=False),
        )
        spec = OutputSpec(format="1:1", motion="slow_push_in", duration_sec=7)
        prompt = director._build_prompt(spec)
        motion = director.motion_presets["slow_push_in"]
        expected = (
            f"{director.subject_template}\n"
            f"{motion}\n"
            f"Duration target: {spec.duration_sec}s at 24fps. No dialogue."
        )
        assert prompt == expected
        assert FRAME_LOCK_CLAUSE not in prompt


class TestProductShotCli:
    def test_module_imports_and_parser_has_flags(self) -> None:
        from scripts import product_shot as ps

        parser = ps.build_parser()
        for flag in (
            "--image",
            "--last-image",
            "--width",
            "--height",
            "--seconds",
            "--prompt",
            "--out",
            "--seed",
            "--timeout",
        ):
            assert flag in parser.format_usage()


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


class TestFreeVideoVramPreflight:
    """The director must release VRAM before submitting a render.

    The FreeVideo resident worker keeps ~13.4 GB of weights. When the GPU cannot host
    them the engine streams them over PCIe for every block, which measured ~80x slower
    (0.57 s -> ~46 s per block, 312 s -> over 1800 s for the same clip).
    """

    @staticmethod
    def _recorder(free_status: int = 200):
        calls: list[tuple[str, str]] = []

        def request_func(method, url, **kwargs):
            calls.append((method, url))
            if url.endswith("/free"):
                return _fake_response(status=free_status, json_data={})
            if url.endswith("/system_stats"):
                return _fake_response(
                    status=200,
                    json_data={"devices": [{"vram_free": 4 * 1024**3}]},
                )
            if url.endswith("/prompt"):
                return _fake_response(status=200, json_data={"prompt_id": "pid-1"})
            return _fake_response(status=200, json_data={})

        return request_func, calls

    @staticmethod
    def _job(tmp_path: Path) -> "JobSpec":
        from video_pipeline.director_base import JobSpec

        return JobSpec(
            source_image=tmp_path / "src.png",
            output_spec=OutputSpec(format="16:9", motion="slow_push_in", duration_sec=7),
            workflow={"1": {"class_type": "LoadImage", "inputs": {"image": "src.png"}}},
            output_path=tmp_path / "out.mp4",
            prompt_text="prompt",
            width=8,
            height=8,
            length_frames=24,
        )

    def test_releases_vram_on_own_instance_before_prompt(self, tmp_path: Path) -> None:
        request_func, calls = self._recorder()
        director = FreeVideoDirector(
            output_dir=tmp_path / "out",
            comfy_url="http://127.0.0.1:8189",
            request_func=request_func,
        )
        director.submit(self._job(tmp_path))

        free_calls = [url for method, url in calls if url.endswith("/free")]
        prompt_calls = [url for method, url in calls if url.endswith("/prompt")]
        assert free_calls == ["http://127.0.0.1:8189/free"]
        assert prompt_calls, "the render must still be submitted"
        assert calls.index(("POST", "http://127.0.0.1:8189/free")) < calls.index(
            ("POST", "http://127.0.0.1:8189/prompt")
        ), "VRAM must be released BEFORE the prompt is submitted"

    def test_extra_urls_are_released_once_each(self, tmp_path: Path) -> None:
        request_func, calls = self._recorder()
        director = FreeVideoDirector(
            output_dir=tmp_path / "out",
            comfy_url="http://127.0.0.1:8189",
            free_vram_urls=["http://127.0.0.1:8188", "http://127.0.0.1:8188"],
            request_func=request_func,
        )
        director.submit(self._job(tmp_path))

        free_calls = [url for method, url in calls if url.endswith("/free")]
        assert free_calls.count("http://127.0.0.1:8189/free") == 1
        assert free_calls.count("http://127.0.0.1:8188/free") == 1
        assert len(free_calls) == 2, "no other instance may be touched"

    def test_no_release_when_disabled(self, tmp_path: Path) -> None:
        request_func, calls = self._recorder()
        director = FreeVideoDirector(
            output_dir=tmp_path / "out",
            comfy_url="http://127.0.0.1:8189",
            free_vram_before_submit=False,
            request_func=request_func,
        )
        director.submit(self._job(tmp_path))

        assert not [url for method, url in calls if url.endswith("/free")]
        assert [url for method, url in calls if url.endswith("/prompt")]

    def test_failing_free_is_tolerated(self, tmp_path: Path) -> None:
        def request_func(method, url, **kwargs):
            if url.endswith("/free"):
                return _fake_response(status=500, text="boom")
            if url.endswith("/system_stats"):
                return _fake_response(status=200, json_data={"devices": [{"vram_free": 0}]})
            if url.endswith("/prompt"):
                return _fake_response(status=200, json_data={"prompt_id": "pid-2"})
            return _fake_response(status=200, json_data={})

        director = FreeVideoDirector(
            output_dir=tmp_path / "out",
            comfy_url="http://127.0.0.1:8189",
            request_func=request_func,
        )
        prompt_id = director.submit(self._job(tmp_path))
        assert prompt_id == "pid-2"

    def test_free_vram_value_is_reported(self, tmp_path: Path) -> None:
        request_func, _calls = self._recorder()
        director = FreeVideoDirector(
            output_dir=tmp_path / "out",
            comfy_url="http://127.0.0.1:8189",
            request_func=request_func,
        )
        director.submit(self._job(tmp_path))
        assert director.last_free_vram_gb == pytest.approx(4.0)

    def test_cli_parser_exposes_free_flags(self) -> None:
        import importlib.util

        spec = importlib.util.spec_from_file_location(
            "product_shot_cli",
            Path(__file__).resolve().parents[1] / "scripts" / "product_shot.py",
        )
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        parser = module.build_parser()
        args = parser.parse_args(
            [
                "--image", "a.png",
                "--width", "736",
                "--height", "1024",
                "--prompt", "p.txt",
                "--out", "o.mp4",
                "--free-url", "http://127.0.0.1:8188",
                "--no-free",
            ]
        )
        assert args.free_url == ["http://127.0.0.1:8188"]
        assert args.no_free is True



def test_freevideo_runner_resolves_a_bare_image_name(tmp_path):
    """--image photo.jpg must not die with a bare FileNotFoundError."""
    import importlib.util
    from pathlib import Path as _P

    script = _P(__file__).resolve().parents[1] / "scripts" / "freevideo_lora_run.py"
    spec = importlib.util.spec_from_file_location("fv_lora_run", script)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)

    scratch = tmp_path / "scratch"
    scratch.mkdir()
    (scratch / "photo.jpg").write_bytes(b"x")

    original = mod.SCRATCH
    mod.SCRATCH = scratch
    try:
        src = _P("photo.jpg")
        assert not src.exists()
        for base in (mod.SCRATCH, mod.LAB / "input", _P.cwd()):
            candidate = base / src.name
            if candidate.exists():
                src = candidate
                break
        assert src.exists()
    finally:
        mod.SCRATCH = original
