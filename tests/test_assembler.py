"""Tests for video_pipeline.assembler."""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path
from unittest import mock

import pytest

from video_pipeline.assembler import (
    AssemblyError,
    AssemblySpec,
    ClipSpec,
    _default_rife_workflow,
    _validate_workflow_against_comfyui,
    build_concat_demuxer_argv,
    build_normalize_argv,
    concat_clips,
    parse_ar,
    probe,
    scale_pad_filter,
    target_resolution,
    validate_workflow_against_object_info,
)


def _rife_object_info_fixture() -> dict:
    """Minimal /object_info slice matching VHS + RIFE on ComfyUI :8188."""
    h264_subwidgets = [
        ["pix_fmt", ["yuv420p", "yuv420p10le"]],
        ["crf", "INT", {"default": 19, "min": 0, "max": 100, "step": 1}],
        ["save_metadata", "BOOLEAN", {"default": True}],
    ]
    return {
        "VHS_LoadVideo": {
            "input": {
                "required": {
                    "video": ["STRING", {"default": ""}],
                    "force_rate": ["INT", {"default": 0}],
                    "force_size": [["Disabled"], {}],
                    "custom_width": ["INT", {"default": 0}],
                    "custom_height": ["INT", {"default": 0}],
                    "frame_load_cap": ["INT", {"default": 0}],
                    "skip_first_frames": ["INT", {"default": 0}],
                    "select_every_nth": ["INT", {"default": 1}],
                },
            },
        },
        "RIFE VFI": {
            "input": {
                "required": {
                    "ckpt_name": [["rife47.pth"], {}],
                    "multiplier": ["INT", {"default": 2}],
                    "fast_mode": ["BOOLEAN", {"default": True}],
                    "ensemble": ["BOOLEAN", {"default": True}],
                    "scale_factor": ["FLOAT", {"default": 1.0}],
                    "clear_cache_after_n_frames": ["INT", {"default": 10}],
                    "dtype": [["float32"], {}],
                    "torch_compile": ["BOOLEAN", {"default": False}],
                    "batch_size": ["INT", {"default": 1}],
                    "frames": ["IMAGE", {}],
                },
            },
        },
        "VHS_VideoCombine": {
            "input": {
                "required": {
                    "images": ["IMAGE", {}],
                    "frame_rate": ["FLOAT", {"default": 8}],
                    "loop_count": ["INT", {"default": 0}],
                    "filename_prefix": ["STRING", {"default": "AnimateDiff"}],
                    "format": [
                        [
                            ["video/h264-mp4", h264_subwidgets],
                            ["video/webm-vp9", [["crf", "INT", {"default": 32}]]],
                        ]
                    ],
                    "pingpong": ["BOOLEAN", {"default": False}],
                    "save_output": ["BOOLEAN", {"default": True}],
                },
            },
        },
    }


def _ffmpeg_available() -> bool:
    return shutil.which("ffmpeg") is not None and shutil.which("ffprobe") is not None


class TestParseAr:
    def test_ratio_strings(self) -> None:
        assert parse_ar("16:9") == pytest.approx(16 / 9)
        assert parse_ar("9:16") == pytest.approx(9 / 16)
        assert parse_ar("1:1") == pytest.approx(1.0)

    def test_float_string_and_number(self) -> None:
        assert parse_ar("0.5625") == pytest.approx(0.5625)
        assert parse_ar(1.777) == pytest.approx(1.777)


class TestTargetResolution:
    def test_even_dimensions(self) -> None:
        clips = [{"width": 101, "height": 99}]
        w, h = target_resolution(clips, target_ar=1.0)
        assert w % 2 == 0
        assert h % 2 == 0

    def test_default_uses_max_aspect_ratio(self) -> None:
        clips = [
            {"width": 1280, "height": 720},
            {"width": 720, "height": 1280},
        ]
        w, h = target_resolution(clips)
        assert w / h == pytest.approx(1280 / 720, rel=0.02)
        assert w >= 1280
        assert h >= 1280

    def test_explicit_target_ar(self) -> None:
        clips = [{"width": 200, "height": 200}]
        w, h = target_resolution(clips, target_ar=9 / 16)
        assert w / h == pytest.approx(9 / 16, rel=0.02)
        assert w >= 200


class TestArgvConstruction:
    def test_scale_pad_filter_contains_expected_ops(self) -> None:
        vf = scale_pad_filter(1920, 1080, fps=24)
        assert "force_original_aspect_ratio=decrease" in vf
        assert "pad=1920:1080" in vf
        assert "fps=24" in vf

    def test_normalize_argv_is_list_without_shell_metacharacters(self) -> None:
        src = Path("/tmp/a.mp4")
        dst = Path("/tmp/b.mp4")
        argv = build_normalize_argv(
            src,
            dst,
            width=1280,
            height=720,
            fps=24,
            has_audio=True,
            ensure_audio=True,
        )
        assert isinstance(argv, list)
        assert argv[0] == "ffmpeg"
        assert "-y" in argv
        vf = argv[argv.index("-vf") + 1]
        assert "force_original_aspect_ratio=decrease" in vf
        assert "pad=1280:720" in vf
        for token in argv:
            assert ";" not in token
            assert "|" not in token
            assert "&" not in token

    def test_normalize_argv_single_input_when_no_external_audio(self) -> None:
        src = Path("/tmp/video.mp4")
        dst = Path("/tmp/out.mp4")
        argv = build_normalize_argv(
            src,
            dst,
            width=1280,
            height=720,
            fps=24,
            has_audio=True,
            ensure_audio=True,
        )
        assert argv.count("-i") == 1
        assert str(src) in argv
        assert "-map" in argv
        map_idx = argv.index("-map")
        assert argv[map_idx + 1] == "0:v:0"
        assert argv[map_idx + 3] == "0:a:0"

    def test_normalize_argv_separate_audio_input(self) -> None:
        video = Path("/tmp/interp.mp4")
        audio = Path("/tmp/upscaled.mp4")
        dst = Path("/tmp/out.mp4")
        argv = build_normalize_argv(
            video,
            dst,
            width=736,
            height=1280,
            fps=48,
            has_audio=True,
            ensure_audio=True,
            audio_src=audio,
        )
        assert argv.count("-i") == 2
        inputs = [argv[i + 1] for i, tok in enumerate(argv) if tok == "-i"]
        assert inputs == [str(video), str(audio)]
        assert argv.count("-map") == 2
        assert "0:v:0" in argv
        assert "1:a:0" in argv
        assert "0:a:0" not in argv

    def test_concat_demuxer_argv(self) -> None:
        argv = build_concat_demuxer_argv(Path("/tmp/list.txt"), Path("/tmp/out.mp4"))
        assert argv[0] == "ffmpeg"
        assert "-y" in argv
        assert "concat" in argv
        assert "-c" in argv and "copy" in argv


def test_probe_parses_ffprobe_json(tmp_path: Path) -> None:
    video = tmp_path / "clip.mp4"
    video.write_bytes(b"x")
    payload = {
        "streams": [
            {
                "codec_type": "video",
                "width": 640,
                "height": 480,
                "avg_frame_rate": "24/1",
            },
            {"codec_type": "audio", "codec_name": "aac"},
        ],
        "format": {"duration": "2.5"},
    }
    proc = mock.MagicMock()
    proc.returncode = 0
    proc.stdout = json.dumps(payload)
    proc.stderr = ""
    with mock.patch("video_pipeline.assembler.subprocess.run", return_value=proc):
        info = probe(video)
    assert info["width"] == 640
    assert info["height"] == 480
    assert info["duration"] == 2.5
    assert info["fps"] == 24.0
    assert info["has_audio"] is True


@pytest.mark.skipif(not _ffmpeg_available(), reason="ffmpeg not installed")
class TestConcatIntegration:
    def _make_clip(self, path: Path, size: str, duration: float, freq: int) -> None:
        cmd = [
            "ffmpeg",
            "-y",
            "-f",
            "lavfi",
            "-i",
            f"testsrc=size={size}:rate=24:duration={duration}",
            "-f",
            "lavfi",
            "-i",
            f"sine=frequency={freq}:duration={duration}",
            "-c:v",
            "libx264",
            "-pix_fmt",
            "yuv420p",
            "-c:a",
            "aac",
            "-shortest",
            str(path),
        ]
        subprocess.run(cmd, check=True, capture_output=True, text=True)

    def test_concat_preserves_audio_and_duration(self, tmp_path: Path) -> None:
        a = tmp_path / "a.mp4"
        b = tmp_path / "b.mp4"
        c = tmp_path / "c.mp4"
        self._make_clip(a, "320x240", 1.0, 440)
        self._make_clip(b, "640x480", 1.5, 880)
        self._make_clip(c, "480x640", 0.8, 660)
        out = tmp_path / "assembled.mp4"

        clips = [ClipSpec(a), ClipSpec(b), ClipSpec(c)]
        concat_clips(clips, out)

        assert out.exists()
        info_out = probe(out)
        info_a, info_b, info_c = probe(a), probe(b), probe(c)
        expected = info_a["duration"] + info_b["duration"] + info_c["duration"]
        assert abs(info_out["duration"] - expected) <= 0.3
        assert info_out["has_audio"] is True

        tw, th = target_resolution([info_a, info_b, info_c])
        assert info_out["width"] == tw
        assert info_out["height"] == th


# Required input names on production ComfyUI (:8188) — regression table for RIFE workflow.
_RIFE_NODE_REQUIRED_INPUTS: dict[str, frozenset[str]] = {
    "VHS_LoadVideo": frozenset(
        {
            "video",
            "force_rate",
            "force_size",
            "custom_width",
            "custom_height",
            "frame_load_cap",
            "skip_first_frames",
            "select_every_nth",
        }
    ),
    "RIFE VFI": frozenset(
        {
            "batch_size",
            "ckpt_name",
            "clear_cache_after_n_frames",
            "dtype",
            "ensemble",
            "fast_mode",
            "frames",
            "multiplier",
            "scale_factor",
            "torch_compile",
        }
    ),
    "VHS_VideoCombine": frozenset(
        {
            "frame_rate",
            "loop_count",
            "filename_prefix",
            "format",
            "pingpong",
            "save_output",
        }
    ),
}


def test_default_rife_workflow_covers_required_node_inputs() -> None:
    workflow = _default_rife_workflow("clip.mp4", target_fps=48.0)
    for node in workflow.values():
        class_type = node["class_type"]
        required = _RIFE_NODE_REQUIRED_INPUTS[class_type]
        workflow_keys = set(node["inputs"].keys())
        missing = required - workflow_keys
        assert not missing, f"{class_type} missing workflow inputs: {sorted(missing)}"


def test_validate_workflow_raises_on_missing_required_input() -> None:
    workflow = _default_rife_workflow("clip.mp4", target_fps=24.0)
    workflow["1"]["inputs"].pop("force_size")

    def fake_fetch(_url: str, class_type: str) -> dict:
        required = {name: ["INT"] for name in _RIFE_NODE_REQUIRED_INPUTS[class_type]}
        return {"input": {"required": required, "optional": {}}}

    with mock.patch(
        "video_pipeline.assembler._fetch_node_object_info",
        side_effect=lambda url, ct: fake_fetch(url, ct),
    ):
        with pytest.raises(AssemblyError, match="missing required input: 'force_size'"):
            _validate_workflow_against_comfyui("http://127.0.0.1:8188", workflow)


def test_concat_needs_audio_from_audio_sources_not_video(tmp_path: Path) -> None:
    """Interpolated video has no audio; external audio_path must still enable audio track."""
    video = tmp_path / "interp.mp4"
    audio = tmp_path / "upscaled.mp4"
    video.write_bytes(b"v")
    audio.write_bytes(b"a")
    out = tmp_path / "assembled.mp4"

    video_info = {
        "width": 736,
        "height": 1280,
        "duration": 3.0,
        "fps": 48.0,
        "has_audio": False,
    }
    audio_info = {
        "width": 736,
        "height": 1280,
        "duration": 3.0,
        "fps": 24.0,
        "has_audio": True,
    }

    normalize_calls: list[dict] = []

    def fake_probe(path: Path) -> dict:
        if path == video:
            return video_info
        if path == audio:
            return audio_info
        raise AssertionError(f"unexpected probe path: {path}")

    def fake_build_normalize_argv(*args, **kwargs):
        normalize_calls.append(kwargs)
        return ["ffmpeg", "-y", str(args[1])]

    with (
        mock.patch("video_pipeline.assembler.probe", side_effect=fake_probe),
        mock.patch("video_pipeline.assembler.build_normalize_argv", side_effect=fake_build_normalize_argv),
        mock.patch("video_pipeline.assembler._run_ffmpeg"),
    ):
        concat_clips([ClipSpec(path=video, audio_path=audio)], out)

    assert len(normalize_calls) == 1
    call = normalize_calls[0]
    assert call["has_audio"] is True
    assert call["ensure_audio"] is True
    assert call["audio_src"] == audio


def test_assemble_spec_dataclass(tmp_path: Path) -> None:
    spec = AssemblySpec(
        clips=[ClipSpec(tmp_path / "a.mp4")],
        out_path=tmp_path / "out.mp4",
        interpolate=False,
    )
    assert spec.target_fps is None


class TestDefaultRifeWorkflow:
    def test_vhs_video_combine_includes_format_subwidgets(self) -> None:
        workflow = _default_rife_workflow("clip.mp4", target_fps=48.0)
        combine = workflow["3"]["inputs"]
        assert combine["format"] == "video/h264-mp4"
        assert combine["pix_fmt"] == "yuv420p"
        assert combine["crf"] == 19
        assert combine["save_metadata"] is True

    def test_preflight_accepts_workflow_with_format_subwidgets(self) -> None:
        workflow = _default_rife_workflow("clip.mp4", target_fps=48.0)
        errors = validate_workflow_against_object_info(workflow, _rife_object_info_fixture())
        assert errors == []

    def test_preflight_rejects_unknown_format_subwidget(self) -> None:
        workflow = _default_rife_workflow("clip.mp4", target_fps=48.0)
        workflow["3"]["inputs"]["not_a_real_widget"] = True
        errors = validate_workflow_against_object_info(workflow, _rife_object_info_fixture())
        assert any("unknown input" in err and "not_a_real_widget" in err for err in errors)

    def test_comfyui_preflight_accepts_format_subwidgets(self) -> None:
        workflow = _default_rife_workflow("clip.mp4", target_fps=48.0)
        fixture = _rife_object_info_fixture()

        def fake_fetch(_url: str, class_type: str) -> dict:
            return fixture[class_type]

        with mock.patch(
            "video_pipeline.assembler._fetch_node_object_info",
            side_effect=lambda url, ct: fake_fetch(url, ct),
        ):
            _validate_workflow_against_comfyui("http://127.0.0.1:8188", workflow)
