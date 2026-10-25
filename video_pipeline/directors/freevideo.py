"""FreeVideo (Video DeltaNet) ComfyUI director implementation."""

from __future__ import annotations

import json
import shutil
import subprocess
import time
import uuid
from pathlib import Path
from typing import Any

import requests
from loguru import logger

from video_pipeline.config import FreeVideoConfig, OutputSpec
from video_pipeline.director_base import Director, JobSpec, VideoResult
from video_pipeline.directors.h3 import (
    DEFAULT_MOTION_PRESETS,
    H3Director,
    upscale_target_dimensions,
)

DEFAULT_COMFY_URL = "http://127.0.0.1:8189"
DEFAULT_OUTPUT_ROOT = Path("G:/FreeVideo_output")
FPS = 24
FREEVIDEO_NODE_CLASS = "FreeVideoGenerate"
LAB_COMFY_URL = "http://127.0.0.1:8189"


class FreeVideoDirectorError(RuntimeError):
    """Raised when FreeVideo director operations fail."""


class FreeVideoDirector(Director):
    """ComfyUI FreeVideo image-to-video director (LAB instance only)."""

    def __init__(
        self,
        output_dir: Path,
        comfy_url: str = DEFAULT_COMFY_URL,
        freevideo_config: FreeVideoConfig | None = None,
        motion_presets: dict[str, str] | None = None,
        subject_template: str = "A studio product shot of the subject in the source image.",
        request_func: Any = requests.request,
        output_root: Path | None = None,
    ) -> None:
        """Initialize FreeVideo director.

        Args:
            output_dir: Directory for pipeline outputs (copied videos).
            comfy_url: ComfyUI API base URL (must be LAB with FreeVideo pack).
            freevideo_config: FreeVideo generation parameters.
            motion_presets: Optional motion preset prompt fragments.
            subject_template: Base subject description for prompts.
            request_func: Injectable HTTP request function.
            output_root: Override for FreeVideo node output root (testing).
        """
        self.output_dir = output_dir
        self.comfy_url = comfy_url.rstrip("/")
        self.freevideo = freevideo_config or FreeVideoConfig()
        self.motion_presets = motion_presets or DEFAULT_MOTION_PRESETS
        self.subject_template = subject_template
        self.client_id = str(uuid.uuid4())
        self._request_func = request_func
        self.output_root = Path(output_root or self.freevideo.output_root)

    def _http(
        self,
        method: str,
        path: str,
        timeout: int = 120,
        **kwargs: Any,
    ) -> requests.Response:
        """Perform an HTTP request to ComfyUI."""
        url = f"{self.comfy_url}{path}"
        response = self._request_func(method, url, timeout=timeout, **kwargs)
        if response.status_code >= 400:
            raise FreeVideoDirectorError(
                f"ComfyUI {method} {path} failed: {response.status_code} {response.text}"
            )
        return response

    def _upload_image(self, image_path: Path) -> str:
        """Upload source image to ComfyUI input folder (same helper as H3)."""
        return H3Director._upload_image(self, image_path)

    def _ensure_freevideo_installed(self) -> None:
        """Verify FreeVideoGenerate is registered on this ComfyUI instance."""
        response = self._http("GET", "/object_info", timeout=60)
        object_info = response.json()
        if FREEVIDEO_NODE_CLASS not in object_info:
            raise FreeVideoDirectorError(
                f"{FREEVIDEO_NODE_CLASS} is not available on ComfyUI at {self.comfy_url}. "
                f"The FreeVideo custom node pack is installed only on the LAB instance "
                f"({LAB_COMFY_URL}), not on PROD (http://127.0.0.1:8188). "
                "Point director_config.comfy_url at LAB or install the pack on this instance."
            )

    def _build_prompt(self, output_spec: OutputSpec) -> str:
        """Build shot-list prompt text from motion preset (same as H3)."""
        motion = self.motion_presets.get(output_spec.motion, output_spec.motion)
        duration = output_spec.duration_sec
        return (
            f"{self.subject_template}\n"
            f"{motion}\n"
            f"Duration target: {duration}s at 24fps. No dialogue."
        )

    def _length_frames(self, seconds: float) -> int:
        """Convert seconds to frame count at 24fps."""
        return max(24, int(seconds * FPS))

    def build_workflow(self, uploaded_name: str, prompt_text: str) -> dict[str, Any]:
        """Build the LoadImage + FreeVideoGenerate graph."""
        fv = self.freevideo
        return {
            "1": {
                "class_type": "LoadImage",
                "inputs": {"image": uploaded_name},
            },
            "2": {
                "class_type": FREEVIDEO_NODE_CLASS,
                "inputs": {
                    "text": prompt_text,
                    "width": fv.width,
                    "height": fv.height,
                    "seconds": fv.seconds,
                    "seed": fv.seed,
                    "first": ["1", 0],
                    "two_pass": fv.two_pass,
                    "base_steps": fv.base_steps,
                    "refine_steps": fv.refine_steps,
                    "preview": fv.preview,
                },
            },
        }

    @staticmethod
    def snapshot_mp4_files(output_root: Path) -> set[Path]:
        """Return the set of all ``*.mp4`` files under ``output_root``."""
        if not output_root.exists():
            return set()
        return set(output_root.rglob("*.mp4"))

    @staticmethod
    def discover_newest_mp4(
        before: set[Path],
        after: set[Path],
    ) -> Path | None:
        """Pick the newest mp4 that appeared between two snapshots."""
        new_files = after - before
        if not new_files:
            return None
        return max(new_files, key=lambda p: p.stat().st_mtime)

    def copy_discovered_video(self, source: Path, dest: Path) -> Path:
        """Copy a discovered FreeVideo output file into the pipeline output dir."""
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, dest)
        logger.info("Copied FreeVideo output {} -> {}", source, dest)
        return dest

    def prepare(self, source_image: Path, output_spec: OutputSpec) -> JobSpec:
        """Prepare a ComfyUI job from source image and output spec."""
        self._ensure_freevideo_installed()

        uploaded_name = self._upload_image(source_image)
        prompt_text = self._build_prompt(output_spec)
        workflow = self.build_workflow(uploaded_name, prompt_text)

        fv = self.freevideo
        length_frames = self._length_frames(fv.seconds)

        stem = source_image.stem
        self.output_dir.mkdir(parents=True, exist_ok=True)
        output_path = self.output_dir / f"{stem}_{output_spec.format.replace(':', 'x')}.mp4"

        return JobSpec(
            source_image=source_image,
            output_spec=output_spec,
            workflow=workflow,
            output_path=output_path,
            prompt_text=prompt_text,
            width=fv.width,
            height=fv.height,
            length_frames=length_frames,
            extra={"uploaded_name": uploaded_name},
        )

    def submit(self, job: JobSpec) -> str:
        """Submit workflow to ComfyUI /prompt endpoint."""
        job.extra["mp4_snapshot_before"] = self.snapshot_mp4_files(self.output_root)
        payload = {"prompt": job.workflow, "client_id": self.client_id}
        response = self._http("POST", "/prompt", json=payload, timeout=60)
        data = response.json()
        prompt_id = data.get("prompt_id")
        if not prompt_id:
            raise FreeVideoDirectorError(f"ComfyUI did not return prompt_id: {data}")
        logger.info("Submitted FreeVideo job prompt_id={}", prompt_id)
        return prompt_id

    def _log_report_metrics(self, report_raw: str) -> None:
        """Log metrics from the FreeVideo report JSON string output."""
        try:
            report = json.loads(report_raw)
        except json.JSONDecodeError:
            logger.warning("FreeVideo report output was not valid JSON")
            return
        if not isinstance(report, dict):
            return
        for key in (
            "vram_peak_bytes",
            "ram_peak_bytes",
            "geometry",
            "profile",
            "sampling_plan",
            "low_memory",
            "thermal",
        ):
            if key in report:
                logger.info("FreeVideo report {}: {}", key, report[key])

    def _extract_report_from_history(self, entry: dict[str, Any]) -> None:
        """Find and log the report string from FreeVideoGenerate node outputs."""
        outputs = entry.get("outputs", {})
        for node_output in outputs.values():
            for key in ("report", "text", "string"):
                items = node_output.get(key)
                if not items:
                    continue
                raw = items[0] if isinstance(items, list) else items
                if isinstance(raw, str) and raw.strip().startswith("{"):
                    self._log_report_metrics(raw)
                    return

    def _extract_video_path(self, history_entry: dict[str, Any], job: JobSpec) -> Path:
        """Locate new FreeVideo output on disk and copy to job.output_path."""
        before = job.extra.get("mp4_snapshot_before", set())
        after = self.snapshot_mp4_files(self.output_root)
        newest = self.discover_newest_mp4(before, after)
        if newest is None:
            status = history_entry.get("status", {})
            if status.get("status_str") == "error":
                messages = status.get("messages", [])
                raise FreeVideoDirectorError(f"ComfyUI render failed: {messages}")
            raise FreeVideoDirectorError(
                f"No new .mp4 appeared under FreeVideo output root {self.output_root} "
                f"for job {job.output_path.name}"
            )
        return self.copy_discovered_video(newest, job.output_path)

    def poll_until_done(self, prompt_id: str, timeout_sec: int) -> VideoResult:
        """Poll ComfyUI history until render completes (no job context)."""
        started = time.time()
        poll_start = time.time()

        while time.time() - poll_start < timeout_sec:
            response = self._http("GET", f"/history/{prompt_id}", timeout=30)
            history = response.json()
            if prompt_id in history:
                entry = history[prompt_id]
                status = entry.get("status", {})
                status_str = status.get("status_str", "")

                if status_str == "error":
                    messages = status.get("messages", [])
                    raise FreeVideoDirectorError(f"Render failed: {messages}")

                if entry.get("outputs") or status_str == "success":
                    render_sec = time.time() - started
                    self._extract_report_from_history(entry)
                    raise FreeVideoDirectorError(
                        "poll_until_done requires job context for FreeVideo output discovery; "
                        "use poll_until_done_with_job()"
                    )

            time.sleep(2.0)

        raise FreeVideoDirectorError(
            f"Timeout waiting for prompt_id={prompt_id} after {timeout_sec}s"
        )

    def poll_until_done_with_job(
        self,
        prompt_id: str,
        timeout_sec: int,
        job: JobSpec,
    ) -> VideoResult:
        """Poll until done and copy FreeVideo output to job.output_path."""
        started = time.time()
        poll_start = time.time()

        while time.time() - poll_start < timeout_sec:
            response = self._http("GET", f"/history/{prompt_id}", timeout=30)
            history = response.json()
            if prompt_id in history:
                entry = history[prompt_id]
                status = entry.get("status", {})
                status_str = status.get("status_str", "")

                if status_str == "error":
                    messages = status.get("messages", [])
                    raise FreeVideoDirectorError(f"Render failed: {messages}")

                if entry.get("outputs") or status_str == "success":
                    render_sec = time.time() - started
                    self._extract_report_from_history(entry)
                    video_path = self._extract_video_path(entry, job)
                    duration_sec = self._probe_duration(video_path)
                    return VideoResult(
                        video_path=video_path,
                        prompt_id=prompt_id,
                        duration_sec=duration_sec,
                        render_sec=render_sec,
                        raw_outputs=entry,
                    )

            time.sleep(2.0)

        raise FreeVideoDirectorError(
            f"Timeout waiting for prompt_id={prompt_id} after {timeout_sec}s"
        )

    def _probe_duration(self, video_path: Path) -> float:
        """Get video duration via ffprobe."""
        proc = subprocess.run(
            [
                "ffprobe",
                "-v",
                "quiet",
                "-print_format",
                "json",
                "-show_format",
                str(video_path),
            ],
            check=False,
            capture_output=True,
            text=True,
            timeout=10,
        )
        if proc.returncode != 0:
            return 0.0
        data = json.loads(proc.stdout)
        return float(data.get("format", {}).get("duration", 0.0))

    def upscale(self, video_path: Path, target_format: str) -> Path:
        """Upscale video to target format dimensions using ffmpeg Lanczos (same as H3)."""
        target_w, target_h = upscale_target_dimensions(target_format)
        upscaled_path = video_path.with_name(f"{video_path.stem}_upscaled.mp4")

        from video_pipeline.assembler import scale_pad_filter

        vf = scale_pad_filter(target_w, target_h)
        proc = subprocess.run(
            [
                "ffmpeg",
                "-y",
                "-i",
                str(video_path),
                "-vf",
                vf,
                "-c:v",
                "libx264",
                "-preset",
                "fast",
                "-crf",
                "18",
                str(upscaled_path),
            ],
            check=False,
            capture_output=True,
            text=True,
            timeout=600,
        )
        if proc.returncode != 0:
            raise FreeVideoDirectorError(f"ffmpeg upscale failed: {proc.stderr}")

        return upscaled_path
