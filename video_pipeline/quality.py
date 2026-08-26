"""Quality gate checks using ffprobe and PIL."""

from __future__ import annotations

import json
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from PIL import Image

from video_pipeline.config import QualityGates


@dataclass
class CheckResult:
    """Result of a single quality check."""

    passed: bool
    actual: Any
    expected: Any
    message: str
    name: str = ""


@dataclass
class QualityReport:
    """Aggregated quality check report."""

    checks: list[CheckResult] = field(default_factory=list)
    overall_passed: bool = True

    def add(self, check: CheckResult) -> None:
        """Add a check result and update overall pass status."""
        self.checks.append(check)
        if not check.passed:
            self.overall_passed = False


def _run_ffprobe(args: list[str], timeout: int = 10) -> subprocess.CompletedProcess[str]:
    """Run ffprobe with capture and no check."""
    return subprocess.run(
        ["ffprobe", *args],
        check=False,
        capture_output=True,
        text=True,
        timeout=timeout,
    )


def _ffprobe_video_info(video: Path) -> dict[str, Any]:
    """Extract video stream info via ffprobe JSON output."""
    proc = _run_ffprobe(
        [
            "-v",
            "quiet",
            "-print_format",
            "json",
            "-show_streams",
            "-show_format",
            str(video),
        ]
    )
    if proc.returncode != 0:
        raise RuntimeError(f"ffprobe failed for {video}: {proc.stderr.strip()}")
    return json.loads(proc.stdout)


def _get_video_stream(info: dict[str, Any]) -> dict[str, Any]:
    """Return the first video stream from ffprobe info."""
    for stream in info.get("streams", []):
        if stream.get("codec_type") == "video":
            return stream
    raise RuntimeError("No video stream found in ffprobe output")


def _source_aspect_ratio(source_image: Path) -> float:
    """Compute aspect ratio (width/height) from a source image."""
    with Image.open(source_image) as img:
        w, h = img.size
    return w / h if h else 0.0


class QualityGate:
    """Quality gate checks for rendered videos."""

    def check_ar(
        self,
        video: Path,
        source_image: Path,
        max_drift: float,
    ) -> CheckResult:
        """Check aspect ratio drift between video and source image.

        Args:
            video: Rendered video path.
            source_image: Original source image path.
            max_drift: Maximum allowed relative drift (e.g. 0.03 = 3%).

        Returns:
            CheckResult with drift value and pass/fail.
        """
        info = _ffprobe_video_info(video)
        stream = _get_video_stream(info)
        vw = int(stream.get("width", 0))
        vh = int(stream.get("height", 0))
        video_ar = vw / vh if vh else 0.0
        source_ar = _source_aspect_ratio(source_image)

        if source_ar == 0.0:
            drift = 0.0
        else:
            drift = abs(video_ar - source_ar) / source_ar

        passed = drift <= max_drift
        message = (
            f"AR drift {drift:.4f} (video={video_ar:.4f}, source={source_ar:.4f})"
            if passed
            else f"AR drift {drift:.4f} exceeds max {max_drift}"
        )
        return CheckResult(
            name="aspect_ratio",
            passed=passed,
            actual=drift,
            expected=max_drift,
            message=message,
        )

    def check_duration(
        self,
        video: Path,
        target_sec: float,
        tolerance: float = 0.5,
    ) -> CheckResult:
        """Check video duration against target.

        Args:
            video: Video file path.
            target_sec: Expected duration in seconds.
            tolerance: Allowed deviation in seconds.

        Returns:
            CheckResult with actual duration and pass/fail.
        """
        info = _ffprobe_video_info(video)
        fmt = info.get("format", {})
        actual = float(fmt.get("duration", 0.0))
        diff = abs(actual - target_sec)
        passed = diff <= tolerance
        message = (
            f"Duration {actual:.2f}s within tolerance of {target_sec}s"
            if passed
            else f"Duration {actual:.2f}s differs from target {target_sec}s by {diff:.2f}s"
        )
        return CheckResult(
            name="duration",
            passed=passed,
            actual=actual,
            expected=target_sec,
            message=message,
        )

    def check_resolution(self, video: Path, min_h: int) -> CheckResult:
        """Check that video height meets minimum resolution.

        Args:
            video: Video file path.
            min_h: Minimum height in pixels.

        Returns:
            CheckResult with actual height and pass/fail.
        """
        info = _ffprobe_video_info(video)
        stream = _get_video_stream(info)
        height = int(stream.get("height", 0))
        passed = height >= min_h
        message = (
            f"Height {height}px meets minimum {min_h}px"
            if passed
            else f"Height {height}px below minimum {min_h}px"
        )
        return CheckResult(
            name="resolution",
            passed=passed,
            actual=height,
            expected=min_h,
            message=message,
        )

    def check_codec(self, video: Path, expected: str) -> CheckResult:
        """Check video codec name.

        Args:
            video: Video file path.
            expected: Expected codec substring (e.g. "h264").

        Returns:
            CheckResult with actual codec and pass/fail.
        """
        info = _ffprobe_video_info(video)
        stream = _get_video_stream(info)
        actual = stream.get("codec_name", "")
        passed = expected.lower() in actual.lower()
        message = (
            f"Codec '{actual}' matches expected '{expected}'"
            if passed
            else f"Codec '{actual}' does not match expected '{expected}'"
        )
        return CheckResult(
            name="codec",
            passed=passed,
            actual=actual,
            expected=expected,
            message=message,
        )

    def check_all(
        self,
        video: Path,
        source_image: Path,
        gates: QualityGates,
        target_duration_sec: float | None = None,
    ) -> QualityReport:
        """Run all quality checks and aggregate results.

        Args:
            video: Rendered video path.
            source_image: Original source image.
            gates: Quality gate thresholds.
            target_duration_sec: Optional expected duration for duration check.

        Returns:
            QualityReport with all check results.
        """
        report = QualityReport()
        report.add(self.check_ar(video, source_image, gates.max_ar_drift))
        report.add(self.check_resolution(video, gates.min_resolution_h))
        report.add(self.check_codec(video, gates.expected_codec))
        if target_duration_sec is not None:
            report.add(self.check_duration(video, target_duration_sec))
        return report
