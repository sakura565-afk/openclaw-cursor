"""Unit tests for ReActor clip face locking; live ComfyUI is optional."""

from __future__ import annotations

import json
import urllib.request
from pathlib import Path
from unittest import mock
from urllib.error import URLError

import pytest
from PIL import Image

from scripts import face_swap_clip as fsc


def test_reactor_workflow_uses_verified_node_schema() -> None:
    workflow = fsc.build_reactor_workflow(
        "target.png",
        "source.jpg",
        "face_lock_test",
        restore_model="GFPGANv1.4.pth",
    )

    assert workflow["1"] == {
        "class_type": "LoadImage",
        "inputs": {"image": "target.png"},
    }
    assert workflow["2"]["inputs"] == {"image": "source.jpg"}
    reactor = workflow["3"]
    assert reactor["class_type"] == "ReActorFaceSwap"
    assert reactor["inputs"] == {
        "enabled": True,
        "input_image": ["1", 0],
        "source_image": ["2", 0],
        "swap_model": "inswapper_128.onnx",
        "facedetection": "retinaface_resnet50",
        "face_restore_model": "GFPGANv1.4.pth",
        # visibility 1.0 = the airbrushed "fake plastic AI skin" look
        "face_restore_visibility": 0.7,
        "codeformer_weight": 0.5,
        "detect_gender_input": "no",
        "detect_gender_source": "no",
        "input_faces_index": "0",
        "source_faces_index": "0",
        "console_log_level": 1,
    }
    assert workflow["4"] == {
        "class_type": "SaveImage",
        "inputs": {"images": ["3", 0], "filename_prefix": "face_lock_test"},
    }


def test_parse_roi_accepts_xywh_and_none() -> None:
    from scripts.face_lock import parse_roi

    assert parse_roi("10,20,300,400") == (10, 20, 300, 400)
    assert parse_roi(None) is None


@pytest.mark.parametrize("value", ["1,2,3", "a,b,c,d", "-1,2,3,4", "1,2,0,4"])
def test_parse_roi_rejects_invalid_values(value: str) -> None:
    from scripts.face_lock import parse_roi

    with pytest.raises(ValueError, match="ROI"):
        parse_roi(value)


def test_face_difference_measures_only_face_roi(tmp_path: Path) -> None:
    from scripts.face_lock import face_difference

    first = tmp_path / "first.png"
    current = tmp_path / "current.png"
    Image.new("RGB", (4, 4), "black").save(first)
    changed_outside = Image.new("RGB", (4, 4), "white")
    for x in range(1, 3):
        for y in range(1, 3):
            changed_outside.putpixel((x, y), (0, 0, 0))
    changed_outside.save(current)

    assert face_difference(first, current, (1, 1, 2, 2)) == 0.0

    changed_inside = changed_outside.copy()
    changed_inside.putpixel((1, 1), (255, 0, 0))
    changed_inside.save(current)
    assert face_difference(first, current, (1, 1, 1, 1)) == 85.0


def test_skip_threshold_zero_disables_reuse() -> None:
    from scripts.face_lock import should_skip

    assert not should_skip(0.0, 0.0)
    assert should_skip(2.0, 2.0)
    assert not should_skip(2.01, 2.0)


def test_reused_roi_keeps_current_background(tmp_path: Path) -> None:
    from scripts.face_lock import reuse_swapped_roi

    swapped = tmp_path / "swapped.png"
    current = tmp_path / "current.png"
    output = tmp_path / "output.png"
    Image.new("RGB", (4, 4), "green").save(swapped)
    Image.new("RGB", (4, 4), "black").save(current)

    reuse_swapped_roi(swapped, current, output, (1, 1, 2, 2))

    with Image.open(output) as result:
        assert result.getpixel((0, 0)) == (0, 0, 0)
        assert result.getpixel((1, 1)) == (0, 128, 0)
        assert result.getpixel((3, 3)) == (0, 0, 0)


def test_encode_command_preserves_source_audio_and_requested_fps(tmp_path: Path) -> None:
    from scripts.face_lock import build_encode_command

    frames = tmp_path / "frames"
    video = tmp_path / "clip.mp4"
    output = tmp_path / "out.mp4"
    command = build_encode_command(frames, video, output, 24.0)

    assert command[command.index("-framerate") + 1] == "24"
    assert str(video) in command
    assert command[command.index("-map", command.index("-map") + 1) + 1] == "1:a:0?"
    assert command[command.index("-c:a") + 1] == "copy"
    assert command[-1] == str(output)


def test_extract_command_honors_max_frames(tmp_path: Path) -> None:
    from scripts.face_lock import build_extract_command

    command = build_extract_command(tmp_path / "clip.mp4", tmp_path / "frames", 6)

    assert command[command.index("-frames:v") + 1] == "6"
    assert command[command.index("-fps_mode") + 1] == "passthrough"
    assert command[-1].endswith("frame_%06d.png")


def test_history_output_resolves_save_image_subfolder(tmp_path: Path) -> None:
    expected = tmp_path / "nested" / "face.png"
    expected.parent.mkdir()
    expected.write_bytes(b"png")
    entry = {
        "outputs": {
            "4": {
                "images": [
                    {"filename": "face.png", "subfolder": "nested", "type": "output"}
                ]
            }
        }
    }

    assert fsc.find_output_image(entry, tmp_path) == expected


def test_comfyui_connection_error_is_actionable(tmp_path: Path) -> None:
    client = fsc.ComfyUIClient(
        "http://127.0.0.1:1", input_dir=tmp_path, output_dir=tmp_path
    )

    with mock.patch("urllib.request.urlopen", side_effect=URLError("refused")):
        with pytest.raises(fsc.ComfyUIError, match="unavailable"):
            client.api("/queue", timeout=0.1)


def test_frame_polling_has_a_hard_timeout(tmp_path: Path) -> None:
    client = fsc.ComfyUIClient(
        "http://127.0.0.1:1", input_dir=tmp_path, output_dir=tmp_path
    )

    with pytest.raises(fsc.FrameTimeout, match="timed out"):
        client.wait_for_output("prompt-id", timeout=0)


def test_frame_timeout_budget_includes_prompt_submission(tmp_path: Path) -> None:
    image = tmp_path / "frame.png"
    source = tmp_path / "source.png"
    image.write_bytes(b"frame")
    source.write_bytes(b"source")
    client = fsc.ComfyUIClient(
        "http://127.0.0.1:1", input_dir=tmp_path, output_dir=tmp_path
    )

    with (
        mock.patch("scripts.face_swap_clip.time.monotonic", side_effect=[0.0, 5.0]),
        mock.patch.object(client, "api", return_value={"prompt_id": "p"}),
        mock.patch.object(client, "wait_for_output", side_effect=fsc.FrameTimeout("stop")) as wait,
    ):
        with pytest.raises(fsc.FrameTimeout, match="stop"):
            client.swap_one(image, source, tmp_path / "out.png", timeout=10)

    assert wait.call_args.kwargs["timeout"] == 5.0


def test_process_frames_reuses_stable_face_and_swaps_changed_face(tmp_path: Path) -> None:
    from scripts.face_lock import process_frames

    frame_dir = tmp_path / "input"
    output_dir = tmp_path / "output"
    frame_dir.mkdir()
    frames = [frame_dir / f"frame_{index:06d}.png" for index in range(3)]
    Image.new("RGB", (4, 4), "black").save(frames[0])
    Image.new("RGB", (4, 4), "black").save(frames[1])
    Image.new("RGB", (4, 4), "white").save(frames[2])
    source = tmp_path / "source.png"
    Image.new("RGB", (4, 4), "red").save(source)

    class FakeClient:
        def __init__(self) -> None:
            self.calls: list[int] = []

        def swap_one(self, image: Path, source: Path, destination: Path, **kwargs: object) -> Path:
            self.calls.append(int(image.stem.rsplit("_", 1)[1]))
            Image.new("RGB", (4, 4), "green").save(destination)
            return destination

    client = FakeClient()
    stats = process_frames(
        frames,
        source,
        output_dir,
        client,
        roi=(0, 0, 2, 2),
        skip_threshold=1.0,
        progress_every=10,
    )

    assert stats == {"total": 3, "swapped": 2, "reused": 1}
    assert client.calls == [0, 2]
    with Image.open(output_dir / frames[1].name) as reused:
        assert reused.getpixel((0, 0)) == (0, 128, 0)
        assert reused.getpixel((3, 3)) == (0, 0, 0)


def test_process_frames_requires_roi_for_skip_heuristic(tmp_path: Path) -> None:
    from scripts.face_lock import process_frames

    with pytest.raises(ValueError, match="--roi"):
        process_frames([], tmp_path / "source.png", tmp_path, object(), skip_threshold=1.0)


def test_parse_frame_rate_preserves_ntsc_rate() -> None:
    from scripts.face_lock import parse_frame_rate

    assert parse_frame_rate("24000/1001") == pytest.approx(23.976023, rel=1e-6)
    with pytest.raises(ValueError, match="frame rate"):
        parse_frame_rate("0/0")


def _live_reactor_schema() -> dict | None:
    """Return the live schema; nothing is submitted to the render queue."""
    try:
        with urllib.request.urlopen(
            f"{fsc.BASE}/object_info/ReActorFaceSwap", timeout=2
        ) as response:
            return json.loads(response.read().decode("utf-8"))
    except (OSError, ValueError):
        return None


LIVE_SCHEMA = _live_reactor_schema()
requires_live_comfyui = pytest.mark.skipif(
    LIVE_SCHEMA is None, reason="live ComfyUI with ReActor is unavailable"
)


@requires_live_comfyui
def test_live_comfyui_reactor_schema_supports_generated_workflow() -> None:
    """Integration: validate the graph against the running service, without rendering."""
    assert LIVE_SCHEMA is not None
    node = LIVE_SCHEMA["ReActorFaceSwap"]
    required = node["input"]["required"]
    optional = node["input"]["optional"]
    workflow = fsc.build_reactor_workflow("target.png", "source.jpg", "schema_check")
    reactor_inputs = workflow["3"]["inputs"]

    assert set(reactor_inputs) == set(required) | {"source_image"}
    assert "source_image" in optional
    assert reactor_inputs["swap_model"] in required["swap_model"][0]
    assert reactor_inputs["face_restore_model"] in required["face_restore_model"][0]
