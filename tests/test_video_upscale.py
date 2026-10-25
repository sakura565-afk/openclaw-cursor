"""Tests for video_pipeline.upscale (no network, no real ComfyUI)."""

from __future__ import annotations

from pathlib import Path
from unittest import mock

import pytest

from video_pipeline.config import UpscaleConfig
from video_pipeline.runner import _director_upscale_config
from video_pipeline.upscale import (
    build_esrgan_workflow,
    build_seedvr2_workflow,
    capabilities_from_object_info,
    free_vram_gb,
    lanczos_upscale,
    mux_audio_from_source,
    release_vram,
    select_backend,
    upscale_video,
)

FAKE_OBJECT_INFO_SEEDVR2 = {
    "SeedVR2VideoUpscaler": {},
    "SeedVR2LoadDiTModel": {},
    "VHS_LoadVideo": {},
    "VHS_VideoCombine": {},
    "UpscaleModelLoader": {},
    "ImageUpscaleWithModel": {},
}

FAKE_OBJECT_INFO_ESRGAN_ONLY = {
    "UpscaleModelLoader": {},
    "ImageUpscaleWithModel": {},
    "VHS_LoadVideo": {},
    "VHS_VideoCombine": {},
}

FAKE_OBJECT_INFO_EMPTY: dict = {}


class TestSelectBackend:
    def test_auto_prefers_seedvr2(self) -> None:
        cfg = UpscaleConfig(backend="auto")
        assert select_backend(cfg, FAKE_OBJECT_INFO_SEEDVR2) == "seedvr2"

    def test_auto_esrgan_when_no_seedvr2(self) -> None:
        cfg = UpscaleConfig(backend="auto")
        assert select_backend(cfg, FAKE_OBJECT_INFO_ESRGAN_ONLY) == "esrgan"

    def test_auto_lanczos_when_no_comfy_nodes(self) -> None:
        cfg = UpscaleConfig(backend="auto")
        assert select_backend(cfg, FAKE_OBJECT_INFO_EMPTY) == "lanczos"

    def test_explicit_seedvr2_missing_falls_back_to_lanczos(self) -> None:
        cfg = UpscaleConfig(backend="seedvr2")
        assert select_backend(cfg, FAKE_OBJECT_INFO_ESRGAN_ONLY) == "lanczos"

    def test_explicit_lanczos(self) -> None:
        cfg = UpscaleConfig(backend="lanczos")
        assert select_backend(cfg, FAKE_OBJECT_INFO_SEEDVR2) == "lanczos"

    def test_capabilities_from_object_info(self) -> None:
        caps = capabilities_from_object_info(FAKE_OBJECT_INFO_SEEDVR2)
        assert caps == {"seedvr2", "esrgan"}


class TestSeedVR2Workflow:
    def test_required_inputs_and_audio_link(self) -> None:
        cfg = UpscaleConfig(resolution=1080, batch_size=5, temporal_overlap=3, blocks_to_swap=32)
        wf = build_seedvr2_workflow("clip.mp4", 24.0, cfg)
        load = wf["1"]["inputs"]
        assert load["force_size"] == "Disabled"
        assert load["video"] == "clip.mp4"
        upscaler = wf["4"]["inputs"]
        assert upscaler["resolution"] == 1080
        assert upscaler["batch_size"] == 5
        assert upscaler["temporal_overlap"] == 3
        combine = wf["5"]["inputs"]
        assert combine["pix_fmt"] == "yuv420p"
        assert combine["crf"] == 19
        assert combine["save_metadata"] is True
        assert combine["audio"] == ["1", 2]
        assert combine["images"] == ["4", 0]

    def test_dit_blocks_to_swap_from_config(self) -> None:
        cfg = UpscaleConfig(blocks_to_swap=16)
        wf = build_seedvr2_workflow("x.mp4", 30.0, cfg)
        assert wf["2"]["inputs"]["blocks_to_swap"] == 16


class TestEsrganWorkflow:
    def test_graph_shape(self) -> None:
        wf = build_esrgan_workflow("in.mp4", 60.0)
        assert wf["2"]["inputs"]["model_name"] == "4x-UltraSharp.pth"
        assert wf["3"]["inputs"]["image"] == ["1", 0]
        combine = wf["4"]["inputs"]
        assert combine["audio"] == ["1", 2]
        assert combine["pix_fmt"] == "yuv420p"
        assert combine["save_metadata"] is True
        assert wf["1"]["inputs"]["force_size"] == "Disabled"


class TestUpscaleConfigDefaults:
    def test_pipeline_backward_compatible(self) -> None:
        from video_pipeline.config import DeliveryConfig, OutputSpec, PipelineConfig

        cfg = PipelineConfig(
            project="p",
            source_dir=Path("media"),
            outputs=[OutputSpec(format="1:1")],
            delivery=DeliveryConfig(telegram_chat=1),
        )
        assert cfg.upscale.backend == "auto"
        assert cfg.upscale.comfyui_url == "http://127.0.0.1:8188"
        assert cfg.upscale.resolution == 1080


class TestLanczosAndAudio:
    def test_lanczos_invokes_ffmpeg(self, tmp_path: Path) -> None:
        src = tmp_path / "in.mp4"
        dst = tmp_path / "out.mp4"
        src.write_bytes(b"x")
        with mock.patch("video_pipeline.upscale.probe", return_value={"has_audio": True, "fps": 24.0}):
            with mock.patch("video_pipeline.upscale._run_ffmpeg") as run_ff:
                lanczos_upscale(src, dst, 1280, 720)
        run_ff.assert_called_once()
        cmd = run_ff.call_args[0][0]
        assert "-c:a" in cmd and "copy" in cmd

    def test_mux_audio_from_source_no_audio_passthrough(self, tmp_path: Path) -> None:
        video = tmp_path / "v.mp4"
        audio_src = tmp_path / "a.mp4"
        out = tmp_path / "out.mp4"
        video.write_bytes(b"video-bytes")
        audio_src.write_bytes(b"audio-src")
        with mock.patch("video_pipeline.upscale.probe", return_value={"has_audio": False}):
            mux_audio_from_source(video, audio_src, out)
        assert out.read_bytes() == b"video-bytes"

    def test_mux_audio_from_source_with_audio(self, tmp_path: Path) -> None:
        video = tmp_path / "v.mp4"
        audio_src = tmp_path / "a.mp4"
        out = tmp_path / "out.mp4"
        video.write_bytes(b"v")
        audio_src.write_bytes(b"a")
        with mock.patch("video_pipeline.upscale.probe", return_value={"has_audio": True}):
            with mock.patch("video_pipeline.upscale._run_ffmpeg") as run_ff:
                mux_audio_from_source(video, audio_src, out)
        run_ff.assert_called_once()


class TestReleaseVram:
    def test_posts_free_on_every_url(self) -> None:
        calls: list[tuple[str, dict]] = []

        def fake_post(url: str, **kwargs: object) -> object:
            calls.append((url, kwargs))
            return mock.Mock(status_code=200)

        urls = ["http://127.0.0.1:8189", "http://127.0.0.1:8189/"]
        succeeded = release_vram(urls, request_func_post=fake_post)
        assert succeeded == urls
        assert len(calls) == 2
        for call_url, kwargs in calls:
            assert call_url.endswith("/free")
            assert kwargs["json"] == {"unload_models": True, "free_memory": True}

    def test_swallows_failing_url_and_returns_others(self) -> None:
        def fake_post(url: str, **kwargs: object) -> object:
            if "8189" in url:
                raise ConnectionError("down")
            return mock.Mock(status_code=200)

        succeeded = release_vram(
            ["http://127.0.0.1:8189", "http://127.0.0.1:8188"],
            request_func_post=fake_post,
        )
        assert succeeded == ["http://127.0.0.1:8188"]


class TestFreeVramGb:
    def test_reads_vram_free_as_gb(self) -> None:
        gib = 5 * 1024**3

        def fake_get(url: str, **kwargs: object) -> object:
            assert url.endswith("/system_stats")
            return mock.Mock(status_code=200, json=lambda: {"devices": [{"vram_free": gib}]})

        assert free_vram_gb("http://127.0.0.1:8188/", request_func_get=fake_get) == 5.0

    def test_returns_none_when_vram_free_missing(self) -> None:
        def fake_get(url: str, **kwargs: object) -> object:
            return mock.Mock(status_code=200, json=lambda: {"devices": [{}]})

        assert free_vram_gb("http://127.0.0.1:8188", request_func_get=fake_get) is None


class TestUpscaleVramPreflight:
    def test_preflight_falls_back_when_vram_below_minimum(self, tmp_path: Path) -> None:
        src = tmp_path / "in.mp4"
        dst = tmp_path / "out.mp4"
        src.write_bytes(b"in")
        cfg = UpscaleConfig(backend="seedvr2", min_free_vram_gb=13.0)
        with mock.patch("video_pipeline.upscale._comfy_reachable", return_value=True):
            with mock.patch("video_pipeline.upscale.release_vram") as rel:
                with mock.patch("video_pipeline.upscale.free_vram_gb", return_value=4.5):
                    with mock.patch("video_pipeline.upscale.lanczos_upscale") as lz:
                        with mock.patch("video_pipeline.upscale.logger") as log:
                            name = upscale_video(
                                src,
                                dst,
                                720,
                                1280,
                                cfg,
                                object_info=FAKE_OBJECT_INFO_SEEDVR2,
                            )
        rel.assert_called_once_with(cfg.free_vram_urls, request_func_post=mock.ANY)
        assert name == "lanczos"
        lz.assert_called_once_with(src, dst, 720, 1280)
        warning = " ".join(str(c) for c in log.warning.call_args_list)
        assert "4.5" in warning and "13" in warning

    def test_preflight_proceeds_when_stats_unavailable(self, tmp_path: Path) -> None:
        src = tmp_path / "in.mp4"
        dst = tmp_path / "out.mp4"
        src.write_bytes(b"in")
        cfg = UpscaleConfig(backend="seedvr2")
        with mock.patch("video_pipeline.upscale._comfy_reachable", return_value=True):
            with mock.patch("video_pipeline.upscale.release_vram"):
                with mock.patch("video_pipeline.upscale.free_vram_gb", return_value=None):
                    with mock.patch("video_pipeline.upscale.probe", return_value={"has_audio": False, "fps": 24.0}):
                        with mock.patch(
                            "video_pipeline.upscale._run_comfy_video_workflow",
                            return_value=dst,
                        ) as run_wf:
                            with mock.patch(
                                "video_pipeline.upscale.apply_target_box",
                                return_value=dst,
                            ):
                                name = upscale_video(
                                    src,
                                    dst,
                                    720,
                                    1280,
                                    cfg,
                                    object_info=FAKE_OBJECT_INFO_SEEDVR2,
                                )
        run_wf.assert_called_once()
        assert name == "seedvr2"

    def test_preflight_allows_learned_backend_when_vram_sufficient(self, tmp_path: Path) -> None:
        src = tmp_path / "in.mp4"
        dst = tmp_path / "out.mp4"
        src.write_bytes(b"in")
        cfg = UpscaleConfig(backend="seedvr2", min_free_vram_gb=13.0)
        with mock.patch("video_pipeline.upscale._comfy_reachable", return_value=True):
            with mock.patch("video_pipeline.upscale.release_vram"):
                with mock.patch("video_pipeline.upscale.free_vram_gb", return_value=15.5):
                    with mock.patch("video_pipeline.upscale.probe", return_value={"has_audio": False, "fps": 24.0}):
                        with mock.patch(
                            "video_pipeline.upscale._run_comfy_video_workflow",
                            return_value=dst,
                        ) as run_wf:
                            with mock.patch(
                                "video_pipeline.upscale.apply_target_box",
                                return_value=dst,
                            ):
                                name = upscale_video(
                                    src,
                                    dst,
                                    720,
                                    1280,
                                    cfg,
                                    object_info=FAKE_OBJECT_INFO_SEEDVR2,
                                )
        run_wf.assert_called_once()
        assert name == "seedvr2"


class TestDirectorUpscaleConfigWiring:
    def test_fills_free_vram_urls_from_director_when_empty(self, tmp_path: Path) -> None:
        from video_pipeline.config import DeliveryConfig, DirectorConfig, OutputSpec, PipelineConfig

        config = PipelineConfig(
            project="p",
            source_dir=tmp_path,
            outputs=[OutputSpec(format="1:1")],
            delivery=DeliveryConfig(telegram_chat=1),
            director_config=DirectorConfig(comfy_url="http://lab:8189"),
        )
        wired = _director_upscale_config(config)
        assert wired.free_vram_urls == ["http://lab:8189"]
        assert config.upscale.free_vram_urls == []

    def test_does_not_override_explicit_free_vram_urls(self, tmp_path: Path) -> None:
        from video_pipeline.config import DeliveryConfig, DirectorConfig, OutputSpec, PipelineConfig

        explicit = ["http://custom:9000"]
        config = PipelineConfig(
            project="p",
            source_dir=tmp_path,
            outputs=[OutputSpec(format="1:1")],
            delivery=DeliveryConfig(telegram_chat=1),
            director_config=DirectorConfig(comfy_url="http://lab:8189"),
            upscale=UpscaleConfig(free_vram_urls=explicit),
        )
        wired = _director_upscale_config(config)
        assert wired.free_vram_urls == explicit
        assert wired.free_vram_urls is not config.upscale.free_vram_urls


class TestUpscaleVideoIntegration:
    def test_unreachable_comfy_falls_back_to_lanczos(self, tmp_path: Path) -> None:
        src = tmp_path / "in.mp4"
        dst = tmp_path / "out.mp4"
        src.write_bytes(b"in")
        cfg = UpscaleConfig(backend="seedvr2")
        with mock.patch("video_pipeline.upscale._comfy_reachable", return_value=False):
            with mock.patch("video_pipeline.upscale.lanczos_upscale") as lz:
                name = upscale_video(src, dst, 720, 1280, cfg, object_info=FAKE_OBJECT_INFO_SEEDVR2)
        assert name == "lanczos"
        lz.assert_called_once_with(src, dst, 720, 1280)
