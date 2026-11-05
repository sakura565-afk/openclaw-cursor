"""Synthetic regression tests for the scene-integrity acceptance gate."""

from __future__ import annotations

import json

import cv2
import numpy as np

from scripts.scene_integrity_gate import analyze_frames
from scripts.scene_integrity_gate.cli import main


def _kitchen_frame() -> np.ndarray:
    frame = np.zeros((96, 128, 3), dtype=np.uint8)
    frame[:] = (45, 95, 105)
    frame[:28] = (65, 120, 130)
    frame[28:42, 12:116] = (180, 185, 175)
    frame[42:, 36:92] = (225, 225, 215)
    frame[10:32, 51:77] = (185, 145, 115)
    frame[52:65, 16:32] = (35, 45, 50)
    return frame


def _macro_frame() -> np.ndarray:
    frame = np.empty((96, 128, 3), dtype=np.uint8)
    frame[:] = (184, 135, 108)
    yy, xx = np.mgrid[:96, :128]
    frame += ((xx + yy) % 7 - 3)[..., None].astype(np.uint8)
    frame[28:88, 60:67] = (95, 55, 62)
    return frame


def test_hard_cut_between_different_scenes_is_rejected() -> None:
    first = _kitchen_frame()
    second = _macro_frame()
    frames = [first.copy() for _ in range(24)] + [second.copy() for _ in range(24)]

    report = analyze_frames(frames)

    assert report["verdict"] == "reject"
    assert any(reason["frame"] == 24 for reason in report["reasons"])
    assert any(reason["metric"] == "scene_change" for reason in report["reasons"])
    disappearance = next(
        reason for reason in report["reasons"] if reason["code"] == "figure_disappeared"
    )
    assert disappearance["measurements"]["context_drop"] >= 0.15
    assert disappearance["measurements"]["object_drop"] >= 0.18


def test_three_frame_scene_transition_is_rejected() -> None:
    first = _kitchen_frame()
    second = _macro_frame()
    frames = [first.copy() for _ in range(24)]
    for step in (1, 2, 3):
        alpha = step / 4.0
        frames.append(cv2.addWeighted(first, 1.0 - alpha, second, alpha, 0.0))
    frames.extend(second.copy() for _ in range(24))

    report = analyze_frames(frames)

    assert report["verdict"] == "reject"
    assert any(24 <= reason["frame"] <= 27 for reason in report["reasons"])


def _zoom(frame: np.ndarray, scale: float) -> np.ndarray:
    height, width = frame.shape[:2]
    crop_width, crop_height = round(width / scale), round(height / scale)
    center_x, center_y = 64, 30
    x1 = max(0, min(width - crop_width, center_x - crop_width // 2))
    y1 = max(0, min(height - crop_height, center_y - crop_height // 2))
    crop = frame[y1:y1 + crop_height, x1:x1 + crop_width]
    return cv2.resize(crop, (width, height), interpolation=cv2.INTER_LINEAR)


def test_smooth_camera_zoom_is_accepted_and_classified() -> None:
    source = _kitchen_frame()
    frames = [_zoom(source, 1.0 + 1.4 * index / 47) for index in range(48)]

    report = analyze_frames(frames)

    assert report["verdict"] == "accept"
    assert report["camera_motion"] == "smooth_zoom"


def test_continuous_static_scene_is_accepted() -> None:
    source = _kitchen_frame()
    frames = [np.roll(source, shift=index % 2, axis=1) for index in range(32)]

    report = analyze_frames(frames)

    assert report["verdict"] == "accept"
    assert report["reasons"] == []


def test_cli_decodes_video_and_writes_json(tmp_path) -> None:
    video_path = tmp_path / "cut.mp4"
    json_path = tmp_path / "report.json"
    writer = cv2.VideoWriter(
        str(video_path), cv2.VideoWriter_fourcc(*"mp4v"), 12.0, (128, 96)
    )
    assert writer.isOpened()
    for index in range(24):
        source = _kitchen_frame() if index < 12 else _macro_frame()
        writer.write(cv2.cvtColor(source, cv2.COLOR_RGB2BGR))
    writer.release()

    assert main(["--video", str(video_path), "--json", str(json_path)]) == 0

    report = json.loads(json_path.read_text(encoding="utf-8"))
    assert report["frame_count"] == 24
    assert report["verdict"] == "reject"
    assert report["reasons"][0]["frame"] == 12
