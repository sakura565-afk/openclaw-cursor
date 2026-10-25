"""Tests for video_pipeline.upscale (no network, no real ComfyUI)."""

from __future__ import annotations

from pathlib import Path
from unittest import mock

import pytest

from video_pipeline.config import UpscaleConfig
from video_pipeline.upscale import (
    build_esrgan_workflow,
    build_seedvr2_workflow,
    capabilities_from_object_info,
    lanczos_upscale,
    mux_audio_from_source,
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
