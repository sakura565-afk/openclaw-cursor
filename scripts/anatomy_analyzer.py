#!/usr/bin/env python3
"""Heuristic anatomy defect detector for ComfyUI generation outputs.

The analyzer operates on a single decoded image (OpenCV BGR ``np.ndarray``) plus
the generation prompt.  It relies on OpenCV skin-tone segmentation and simple
geometric heuristics for the bulk of the work and optionally uses ``insightface``
for face-landmark detection when the model is available.  Every detector returns
a boolean verdict (or a ``dict`` for the color-roulette check) and records a
confidence score in :attr:`AnatomyAnalyzer.confidences` so callers can apply
per-rule confidence thresholds.

The heuristics are intentionally lightweight so they stay fast (<1s per image)
and never require a GPU, avoiding CUDA conflicts with a co-located ComfyUI.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

import numpy as np

try:  # OpenCV is a hard dependency for real analysis but keep import defensive.
    import cv2  # type: ignore
except Exception:  # pragma: no cover - environment without cv2
    cv2 = None  # type: ignore


# --- prompt keyword vocabularies -------------------------------------------------

NSFW_KEYWORDS = (
    "nude",
    "naked",
    "nsfw",
    "topless",
    "breast",
    "nipple",
    "penetration",
    "sex",
    "explicit",
    "erotic",
    "pussy",
    "penis",
)
PENETRATION_KEYWORDS = ("penetration", "penetrate", "insertion", "insert", "vaginal", "anal")
TOY_KEYWORDS = ("toy", "dildo", "vibrator", "plug")
POSE_LYING_KEYWORDS = ("lying", "lay", "reclining", "sand", "supine", "prone")
POSE_STANDING_KEYWORDS = ("standing", "stand", "walk", "shore")
POSE_KNEEL_KEYWORDS = ("kneel", "squat", "crouch")


# --- lazy insightface model ------------------------------------------------------

_FACE_APP: Any = None
_FACE_APP_TRIED = False


def _get_face_app() -> Any:
    """Return a cached CPU-only insightface ``FaceAnalysis`` app or ``None``.

    insightface downloads model weights on first use, so this is best-effort:
    any failure (missing package, no weights, no network) yields ``None`` and
    callers fall back to heuristics.
    """

    global _FACE_APP, _FACE_APP_TRIED
    if _FACE_APP_TRIED:
        return _FACE_APP
    _FACE_APP_TRIED = True
    try:  # pragma: no cover - exercised only when insightface is installed
        from insightface.app import FaceAnalysis  # type: ignore

        app = FaceAnalysis(name="buffalo_l", providers=["CPUExecutionProvider"])
        app.prepare(ctx_id=-1, det_size=(640, 640))
        _FACE_APP = app
    except Exception:
        _FACE_APP = None
    return _FACE_APP


class AnatomyAnalyzer:
    """Detect common ComfyUI anatomy defects on a single image."""

    def __init__(
        self,
        image: np.ndarray,
        prompt: str,
        rules: Optional[Dict[str, Any]] = None,
    ) -> None:
        if image is None or not hasattr(image, "shape"):
            raise ValueError("image must be a numpy ndarray (OpenCV BGR image)")
        self.image = image
        self.prompt = prompt or ""
        self.prompt_lc = self.prompt.lower()
        self.rules = rules or {}
        self.height, self.width = image.shape[:2]
        self.area = float(max(1, self.height * self.width))
        self.confidences: Dict[str, float] = {}
        self._skin_mask_cache: Optional[np.ndarray] = None
        self._contours_cache: Optional[List[Any]] = None

    # -- prompt helpers ----------------------------------------------------------

    @property
    def is_nsfw(self) -> bool:
        return any(k in self.prompt_lc for k in NSFW_KEYWORDS)

    def _prompt_has(self, keywords: tuple[str, ...]) -> bool:
        return any(k in self.prompt_lc for k in keywords)

    # -- skin segmentation -------------------------------------------------------

    def skin_mask(self) -> np.ndarray:
        """Binary skin-tone mask combining HSV and YCrCb ranges."""

        if self._skin_mask_cache is not None:
            return self._skin_mask_cache
        if cv2 is None:  # pragma: no cover - cv2 always present in tests
            self._skin_mask_cache = np.zeros((self.height, self.width), dtype=np.uint8)
            return self._skin_mask_cache

        bgr = self.image if self.image.ndim == 3 else cv2.cvtColor(self.image, cv2.COLOR_GRAY2BGR)
        hsv = cv2.cvtColor(bgr, cv2.COLOR_BGR2HSV)
        ycrcb = cv2.cvtColor(bgr, cv2.COLOR_BGR2YCrCb)

        hsv_low = cv2.inRange(hsv, np.array([0, 30, 60]), np.array([25, 175, 255]))
        hsv_high = cv2.inRange(hsv, np.array([170, 30, 60]), np.array([180, 175, 255]))
        hsv_mask = cv2.bitwise_or(hsv_low, hsv_high)
        ycrcb_mask = cv2.inRange(ycrcb, np.array([0, 133, 85]), np.array([255, 180, 135]))

        mask = cv2.bitwise_and(hsv_mask, ycrcb_mask)
        kernel = np.ones((5, 5), np.uint8)
        mask = cv2.morphologyEx(mask, cv2.MORPH_OPEN, kernel)
        mask = cv2.morphologyEx(mask, cv2.MORPH_CLOSE, kernel)
        self._skin_mask_cache = mask
        return mask

    def _skin_contours(self) -> List[Any]:
        if self._contours_cache is not None:
            return self._contours_cache
        mask = self.skin_mask()
        if cv2 is None:
            self._contours_cache = []
            return self._contours_cache
        contours, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
        min_area = 0.002 * self.area
        self._contours_cache = [c for c in contours if cv2.contourArea(c) >= min_area]
        return self._contours_cache

    # -- face landmarks ----------------------------------------------------------

    def detect_face_landmarks(self) -> List[Dict[str, Any]]:
        """Return insightface face detections (empty when unavailable)."""

        app = _get_face_app()
        if app is None or cv2 is None:
            self.confidences["face_landmarks"] = 0.0
            return []
        try:  # pragma: no cover - requires downloaded weights
            faces = app.get(self.image)
        except Exception:
            self.confidences["face_landmarks"] = 0.0
            return []
        results: List[Dict[str, Any]] = []
        for face in faces:
            results.append(
                {
                    "bbox": [float(x) for x in getattr(face, "bbox", [])],
                    "kps": getattr(face, "kps", None).tolist()
                    if getattr(face, "kps", None) is not None
                    else [],
                    "det_score": float(getattr(face, "det_score", 0.0)),
                }
            )
        self.confidences["face_landmarks"] = 1.0 if results else 0.0
        return results

    # -- skin region statistics --------------------------------------------------

    def count_skin_regions(self) -> Dict[str, Any]:
        """Classify skin contours into hand/arm/torso/foot-like buckets."""

        contours = self._skin_contours()
        total_skin = sum(cv2.contourArea(c) for c in contours) if cv2 is not None else 0.0
        hand_like = 0
        arm_like = 0
        torso_like = 0
        lower_band_start = self.height * 0.70
        foot_like = 0
        regions: List[Dict[str, Any]] = []

        for c in contours:
            area = float(cv2.contourArea(c))
            x, y, w, h = cv2.boundingRect(c)
            aspect = max(w, h) / max(1.0, float(min(w, h)))
            ratio = area / self.area
            cy = y + h / 2.0
            is_hand = 0.002 <= ratio <= 0.03 and aspect < 2.2
            is_arm = ratio >= 0.01 and aspect >= 2.2
            is_torso = ratio > 0.15
            if is_torso:
                torso_like += 1
            elif is_arm:
                arm_like += 1
            elif is_hand:
                hand_like += 1
            if cy >= lower_band_start and ratio <= 0.03:
                foot_like += 1
            regions.append(
                {"area_ratio": ratio, "bbox": [x, y, w, h], "aspect": aspect, "cy": cy}
            )

        return {
            "regions": len(contours),
            "total_area_ratio": total_skin / self.area,
            "hand_like": hand_like,
            "arm_like": arm_like,
            "torso_like": torso_like,
            "foot_like": foot_like,
            "details": regions,
        }

    # -- individual defect detectors ---------------------------------------------

    def detect_extra_limbs(self) -> bool:
        """Flag 3+ hand-like AND 3+ arm-like skin regions."""

        stats = self.count_skin_regions()
        hands = stats["hand_like"]
        arms = stats["arm_like"]
        detected = hands > 2 and arms > 2
        if detected:
            conf = min(1.0, 0.6 + 0.1 * (hands - 2) + 0.1 * (arms - 2))
        else:
            conf = 0.0
        self.confidences["extra_limbs"] = conf
        return detected

    def detect_fused_hands(self) -> bool:
        """Large connected skin blob with too few finger extensions -> fusion."""

        contours = self._skin_contours()
        if not contours or cv2 is None:
            self.confidences["fused_hands"] = 0.0
            return False
        largest = max(contours, key=cv2.contourArea)
        area_ratio = cv2.contourArea(largest) / self.area
        finger_defects = self._count_convexity_defects(largest)
        detected = area_ratio > 0.15 and finger_defects < 4
        conf = 0.0
        if detected:
            conf = min(1.0, 0.6 + (area_ratio - 0.15))
        self.confidences["fused_hands"] = conf
        return detected

    def _count_convexity_defects(self, contour: Any) -> int:
        try:
            hull = cv2.convexHull(contour, returnPoints=False)
            if hull is None or len(hull) <= 3:
                return 0
            defects = cv2.convexityDefects(contour, hull)
            if defects is None:
                return 0
            depth_threshold = 0.02 * max(self.width, self.height) * 256
            return int(np.sum(defects[:, 0, 3] > depth_threshold))
        except Exception:
            return 0

    def detect_bad_feet(self) -> bool:
        """Count small toe-like contours in the lower 30% of the image."""

        contours = self._skin_contours()
        if cv2 is None:
            self.confidences["bad_feet"] = 0.0
            return False
        lower_start = self.height * 0.70
        toe_like = 0
        finger_like_toe = False
        for c in contours:
            area = float(cv2.contourArea(c))
            x, y, w, h = cv2.boundingRect(c)
            cy = y + h / 2.0
            ratio = area / self.area
            if cy < lower_start:
                continue
            if ratio <= 0.02:
                toe_like += 1
                aspect = h / max(1.0, float(w))
                if aspect >= 2.0:  # tall & thin -> finger-shaped toe
                    finger_like_toe = True
        detected = toe_like > 5 or finger_like_toe
        conf = 0.0
        if detected:
            conf = min(1.0, 0.6 + 0.08 * max(0, toe_like - 5) + (0.2 if finger_like_toe else 0.0))
        self.confidences["bad_feet"] = conf
        return detected

    def detect_asymmetric_breasts(self) -> bool:
        """Compare left/right skin blobs in the upper torso band (NSFW only)."""

        if not self.is_nsfw:
            self.confidences["asymmetric_breasts"] = 0.0
            return False
        threshold = float(
            self.rules.get("asymmetric_breasts", {}).get("area_diff_threshold", 0.25)
        )
        contours = self._skin_contours()
        if cv2 is None:
            self.confidences["asymmetric_breasts"] = 0.0
            return False
        band_top = self.height * 0.15
        band_bottom = self.height * 0.60
        mid_x = self.width / 2.0
        left_area = 0.0
        right_area = 0.0
        for c in contours:
            area = float(cv2.contourArea(c))
            x, y, w, h = cv2.boundingRect(c)
            cx = x + w / 2.0
            cy = y + h / 2.0
            ratio = area / self.area
            if not (band_top <= cy <= band_bottom):
                continue
            if ratio > 0.15:  # torso, not a breast
                continue
            if cx < mid_x:
                left_area = max(left_area, area)
            else:
                right_area = max(right_area, area)
        if left_area <= 0 or right_area <= 0:
            self.confidences["asymmetric_breasts"] = 0.0
            return False
        diff = abs(left_area - right_area) / max(left_area, right_area)
        detected = diff > threshold
        self.confidences["asymmetric_breasts"] = min(1.0, diff) if detected else 0.0
        return detected

    def detect_toy_color_roulette(self) -> Dict[str, Any]:
        """Detect a lower-center object and check for off-flesh toy coloring."""

        rule = self.rules.get("toy_color_roulette", {})
        hue_ranges = rule.get("hue_ranges", {"purple": [280, 320], "pink": [300, 340]})
        result: Dict[str, Any] = {
            "toy_color": None,
            "hue_deg": None,
            "roulette_flag": False,
            "confidence": 0.0,
        }
        if cv2 is None:
            self.confidences["toy_color_roulette"] = 0.0
            return result

        # Region of interest: lower-center third of the image.
        y0 = int(self.height * 0.55)
        x0 = int(self.width * 0.25)
        x1 = int(self.width * 0.75)
        roi = self.image[y0 : self.height, x0:x1]
        if roi.size == 0:
            self.confidences["toy_color_roulette"] = 0.0
            return result

        skin = self.skin_mask()[y0 : self.height, x0:x1]
        non_skin = cv2.bitwise_not(skin)
        # Ignore near-white/background by requiring some saturation OR darkness.
        hsv_roi = cv2.cvtColor(roi, cv2.COLOR_BGR2HSV)
        sat = hsv_roi[:, :, 1]
        val = hsv_roi[:, :, 2]
        colored = cv2.bitwise_or(
            cv2.inRange(sat, 60, 255), cv2.inRange(val, 0, 60)
        )
        object_mask = cv2.bitwise_and(non_skin, colored)
        roi_area = roi.shape[0] * roi.shape[1]
        if roi_area == 0:
            self.confidences["toy_color_roulette"] = 0.0
            return result

        # A toy is a compact blob, not a diffuse background: isolate the largest
        # connected component and reject it if it fills nearly the whole ROI.
        contours, _ = cv2.findContours(
            object_mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE
        )
        contours = [c for c in contours if cv2.contourArea(c) >= 0.02 * roi_area]
        if not contours:
            self.confidences["toy_color_roulette"] = 0.0
            return result
        largest = max(contours, key=cv2.contourArea)
        bx, by, bw, bh = cv2.boundingRect(largest)
        if bw >= 0.9 * roi.shape[1] and bh >= 0.9 * roi.shape[0]:
            # Fills the ROI -> this is background, not a distinct object.
            self.confidences["toy_color_roulette"] = 0.0
            return result

        blob_mask = np.zeros(object_mask.shape, dtype=np.uint8)
        cv2.drawContours(blob_mask, [largest], -1, 255, thickness=cv2.FILLED)
        object_pixels = int(cv2.contourArea(largest))

        mean_hsv = cv2.mean(hsv_roi, mask=blob_mask)
        hue_deg = float(mean_hsv[0]) * 2.0
        mean_val = float(mean_hsv[2])
        result["hue_deg"] = round(hue_deg, 1)

        flagged = False
        color_label = None
        for label, (lo, hi) in hue_ranges.items():
            if lo <= hue_deg <= hi:
                flagged = True
                color_label = label
                break
        if not flagged and mean_val < 45:  # pure-black toy
            flagged = True
            color_label = "black"
        result["toy_color"] = color_label
        result["roulette_flag"] = flagged
        conf = 0.0
        if flagged:
            conf = min(1.0, 0.55 + object_pixels / float(roi_area))
        result["confidence"] = conf
        self.confidences["toy_color_roulette"] = conf
        return result

    def detect_penetration_miss(self) -> bool:
        """NSFW penetration prompt but no plausible contact region found."""

        if not (self.is_nsfw and self._prompt_has(PENETRATION_KEYWORDS)):
            self.confidences["penetration_miss"] = 0.0
            return False
        contours = self._skin_contours()
        # Look for two distinct skin/object regions meeting near lower-center.
        cx_lo = self.width * 0.30
        cx_hi = self.width * 0.70
        cy_lo = self.height * 0.45
        central_regions = 0
        if cv2 is not None:
            for c in contours:
                x, y, w, h = cv2.boundingRect(c)
                cx = x + w / 2.0
                cy = y + h / 2.0
                if cx_lo <= cx <= cx_hi and cy >= cy_lo:
                    central_regions += 1
        # A toy/insertable object also counts as contact.
        toy = self.detect_toy_color_roulette()
        has_object = toy["roulette_flag"] or central_regions >= 2
        detected = not has_object
        self.confidences["penetration_miss"] = 0.6 if detected else 0.0
        return detected

    # -- pose --------------------------------------------------------------------

    def estimate_pose(self) -> str:
        """Very rough pose estimate from the overall skin bounding box."""

        contours = self._skin_contours()
        if not contours or cv2 is None:
            return "unknown"
        xs, ys, xe, ye = self.width, self.height, 0, 0
        for c in contours:
            x, y, w, h = cv2.boundingRect(c)
            xs = min(xs, x)
            ys = min(ys, y)
            xe = max(xe, x + w)
            ye = max(ye, y + h)
        bw = max(1, xe - xs)
        bh = max(1, ye - ys)
        if bw > bh * 1.3:
            return "lying"
        if bh > bw * 1.1:
            return "standing"
        return "sitting"

    def verify_pose_consistency(self, expected_pose: str) -> bool:
        """Return True when the detected pose is consistent with ``expected_pose``."""

        expected = (expected_pose or "").lower()
        detected = self.estimate_pose()
        if detected == "unknown":
            self.confidences["pose_mismatch"] = 0.0
            return True  # cannot disprove; treat as consistent

        expected_lying = any(k in expected for k in POSE_LYING_KEYWORDS)
        expected_upright = any(
            k in expected for k in POSE_STANDING_KEYWORDS + POSE_KNEEL_KEYWORDS
        )

        consistent = True
        if expected_upright and detected == "lying":
            consistent = False
        elif expected_lying and detected == "standing":
            consistent = False
        self.confidences["pose_mismatch"] = 0.0 if consistent else 0.7
        return consistent

    # -- aggregation -------------------------------------------------------------

    def run_all(self) -> Dict[str, Any]:
        """Run every enabled detector and return structured findings."""

        anatomical = self.rules if self.rules else {}

        def enabled(name: str) -> bool:
            rule = anatomical.get(name)
            if rule is None:
                return True
            return bool(rule.get("enabled", True))

        findings: Dict[str, Any] = {}
        if enabled("extra_limbs"):
            findings["extra_limbs"] = {
                "detected": self.detect_extra_limbs(),
                "confidence": self.confidences.get("extra_limbs", 0.0),
            }
        if enabled("fused_hands"):
            findings["fused_hands"] = {
                "detected": self.detect_fused_hands(),
                "confidence": self.confidences.get("fused_hands", 0.0),
            }
        if enabled("bad_feet"):
            findings["bad_feet"] = {
                "detected": self.detect_bad_feet(),
                "confidence": self.confidences.get("bad_feet", 0.0),
            }
        if enabled("asymmetric_breasts"):
            findings["asymmetric_breasts"] = {
                "detected": self.detect_asymmetric_breasts(),
                "confidence": self.confidences.get("asymmetric_breasts", 0.0),
            }
        if enabled("toy_color_roulette"):
            toy = self.detect_toy_color_roulette()
            findings["toy_color_roulette"] = {
                "detected": bool(toy["roulette_flag"]),
                "confidence": toy["confidence"],
                "toy_color": toy["toy_color"],
                "hue_deg": toy["hue_deg"],
            }
        if enabled("penetration_miss"):
            findings["penetration_miss"] = {
                "detected": self.detect_penetration_miss(),
                "confidence": self.confidences.get("penetration_miss", 0.0),
            }
        return findings
