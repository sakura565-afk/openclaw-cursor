"""Build a pelvis mask from body-pose landmarks, never from scene coordinates."""
from __future__ import annotations

from dataclasses import dataclass
from math import atan2, degrees, hypot
from pathlib import Path
from typing import Any

import cv2
import numpy as np


@dataclass(frozen=True)
class PoseEstimate:
    """Normalized landmarks needed to locate the groin."""

    left_hip: tuple[float, float]
    right_hip: tuple[float, float]
    left_knee: tuple[float, float]
    right_knee: tuple[float, float]
    visibility: float
    left_shoulder: tuple[float, float] | None = None
    right_shoulder: tuple[float, float] | None = None
    hip_visibility: float | None = None
    knee_visibility: float | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "left_hip": list(self.left_hip),
            "right_hip": list(self.right_hip),
            "left_knee": list(self.left_knee),
            "right_knee": list(self.right_knee),
            "visibility": self.visibility,
            "left_shoulder": list(self.left_shoulder) if self.left_shoulder else None,
            "right_shoulder": list(self.right_shoulder) if self.right_shoulder else None,
            "hip_visibility": self.hip_visibility,
            "knee_visibility": self.knee_visibility,
        }


def _pixel(point: tuple[float, float], width: int, height: int) -> np.ndarray:
    return np.array([point[0] * width, point[1] * height], dtype=np.float64)


def build_mask_from_pose(
    size: tuple[int, int],
    pose: PoseEstimate,
    *,
    center_shift: float = 0.16,
    width_scale: float = 1.20,
    height_scale: float = 0.34,
    min_visibility: float = 0.50,
) -> np.ndarray:
    """Return a binary ellipse centred below the hip line, towards the knees.

    The ellipse follows the hip axis. Its position and size therefore come from
    the detected pose and naturally rescale between 853x1280 and 810x991 stills.
    """
    width, height = size
    if width <= 0 or height <= 0:
        raise ValueError("image size must be positive")
    hip_confidence = pose.hip_visibility if pose.hip_visibility is not None else pose.visibility
    if hip_confidence < min_visibility:
        raise ValueError(
            f"pose visibility {hip_confidence:.3f} is below {min_visibility:.3f}"
        )
    left_hip = _pixel(pose.left_hip, width, height)
    right_hip = _pixel(pose.right_hip, width, height)
    left_knee = _pixel(pose.left_knee, width, height)
    right_knee = _pixel(pose.right_knee, width, height)
    hip_center = (left_hip + right_hip) / 2.0
    knee_center = (left_knee + right_knee) / 2.0
    hip_vector = right_hip - left_hip
    down_vector = knee_center - hip_center
    hip_width = float(np.linalg.norm(hip_vector))
    leg_length = float(np.linalg.norm(down_vector))
    if hip_width < width * 0.025:
        raise ValueError("hip geometry is too small for a reliable pelvis mask")
    reference_length = max(hip_width, min(leg_length, hip_width * 2.0))
    if leg_length >= max(height * 0.04, hip_width * 0.35):
        down_unit = down_vector / leg_length
    elif pose.left_shoulder and pose.right_shoulder:
        # Seated poses often have knees level with the hips. Shoulder -> hip
        # supplies the body-down direction without any scene-specific pixels.
        shoulder_center = (
            _pixel(pose.left_shoulder, width, height)
            + _pixel(pose.right_shoulder, width, height)
        ) / 2.0
        torso_vector = hip_center - shoulder_center
        torso_length = float(np.linalg.norm(torso_vector))
        if torso_length < height * 0.04:
            raise ValueError("torso geometry is too small for a seated pelvis mask")
        down_unit = torso_vector / torso_length
    else:
        raise ValueError("knee geometry is unreliable and shoulder landmarks are absent")
    center = hip_center + down_unit * (center_shift * reference_length)
    diameter_x = max(12.0, hip_width * width_scale)
    diameter_y = max(12.0, reference_length * height_scale)
    angle = degrees(atan2(float(hip_vector[1]), float(hip_vector[0])))
    center_xy = (
        int(np.clip(round(center[0]), 0, width - 1)),
        int(np.clip(round(center[1]), 0, height - 1)),
    )
    axes = (max(6, round(diameter_x / 2)), max(6, round(diameter_y / 2)))
    mask = np.zeros((height, width), dtype=np.uint8)
    cv2.ellipse(mask, center_xy, axes, angle, 0, 360, 255, -1, cv2.LINE_8)
    return mask


def detect_pose(image_rgb: np.ndarray, *, min_visibility: float = 0.50) -> PoseEstimate:
    """Detect body landmarks with MediaPipe Pose on CPU (optional dependency)."""
    if image_rgb.ndim != 3 or image_rgb.shape[2] != 3:
        raise ValueError("expected an HxWx3 RGB image")
    try:
        import mediapipe as mp
    except ImportError as exc:
        raise RuntimeError(
            "automatic pose masks need mediapipe==0.10.21; "
            "install it in a CPU tools environment or supply an existing mask"
        ) from exc
    height, width = image_rgb.shape[:2]
    with mp.solutions.pose.Pose(
        static_image_mode=True,
        model_complexity=2,
        enable_segmentation=False,
        min_detection_confidence=min_visibility,
    ) as detector:
        result = detector.process(np.ascontiguousarray(image_rgb, dtype=np.uint8))
    if not result.pose_landmarks:
        raise RuntimeError("MediaPipe Pose found no body landmarks")
    landmarks = result.pose_landmarks.landmark
    ids = {
        "left_shoulder": 11,
        "right_shoulder": 12,
        "left_hip": 23,
        "right_hip": 24,
        "left_knee": 25,
        "right_knee": 26,
    }
    points: dict[str, tuple[float, float]] = {}
    visibilities = []
    for name, index in ids.items():
        landmark = landmarks[index]
        points[name] = (
            float(np.clip(landmark.x, 0.0, 1.0)),
            float(np.clip(landmark.y, 0.0, 1.0)),
        )
        visibilities.append(float(landmark.visibility))
    return PoseEstimate(
        **points,
        visibility=min(visibilities),
        hip_visibility=min(float(landmarks[index].visibility) for index in (23, 24)),
        knee_visibility=min(float(landmarks[index].visibility) for index in (25, 26)),
    )


def build_mask_from_image(
    image_rgb: np.ndarray,
    mask_recipe: dict[str, Any] | None = None,
) -> tuple[np.ndarray, PoseEstimate]:
    config = dict(mask_recipe or {})
    pose = detect_pose(
        image_rgb, min_visibility=float(config.get("min_visibility", 0.50))
    )
    allowed = {"center_shift", "width_scale", "height_scale", "min_visibility"}
    kwargs = {key: float(value) for key, value in config.items() if key in allowed}
    height, width = image_rgb.shape[:2]
    return build_mask_from_pose((width, height), pose, **kwargs), pose


def save_mask(path: str | Path, mask: np.ndarray) -> Path:
    output = Path(path)
    output.parent.mkdir(parents=True, exist_ok=True)
    if not cv2.imwrite(str(output), mask):
        raise OSError(f"could not write mask: {output}")
    return output
