"""H3 MiniMax ComfyUI director implementation."""

from __future__ import annotations

import copy
import json
import random
import subprocess
import time
import uuid
from pathlib import Path
from typing import Any

import requests
from loguru import logger

from video_pipeline.config import OutputSpec
from video_pipeline.director_base import Director, JobSpec, VideoResult

DEFAULT_COMFY_URL = "http://127.0.0.1:8183"
DEFAULT_WORKFLOW_PATH = Path("workflows/minimax_h3_hero_short.json")
FPS = 24

# Exact H3 ResolutionSelector aspect ratio strings and target dimensions.
FORMAT_MAP: dict[str, dict[str, Any]] = {
    "1:1": {"aspect_ratio": "1:1 (Square)", "width": 736, "height": 736},
    "9:16": {"aspect_ratio": "9:16 (Vertical)", "width": 736, "height": 1280},
    "16:9": {"aspect_ratio": "16:9 (Widescreen)", "width": 1280, "height": 720},
    "3:4": {"aspect_ratio": "3:4 (Portrait)", "width": 624, "height": 832},
    "4:3": {"aspect_ratio": "4:3 (Landscape)", "width": 832, "height": 624},
    "2:3": {"aspect_ratio": "2:3 (Portrait)", "width": 544, "height": 832},
}

DEFAULT_MOTION_PRESETS: dict[str, str] = {
    "slow_push_in": (
        "SHOT 1 [0.0s-2.5s]: The scene opens exactly on the source image; "
        "the camera performs a slow, steady push-in.\n"
        "SHOT 2 [2.5s-5.0s]: The camera continues a smooth orbit to the right.\n"
        "SHOT 3 [5.0s-end]: Slow settle to a static hero angle."
    ),
    "static": (
        "SHOT 1 [0.0s-end]: Static hero shot holding exactly on the source composition "
        "with subtle ambient motion only."
    ),
    "slow_turn": (
        "SHOT 1 [0.0s-2.5s]: Opens on the source image; slow turn to reveal depth.\n"
        "SHOT 2 [2.5s-end]: Continues a gentle orbit and settles."
    ),
    "pan_left": (
        "SHOT 1 [0.0s-end]: Smooth pan from right to left across the subject "
        "while maintaining focus."
    ),
}


class H3DirectorError(RuntimeError):
    """Raised when H3 director operations fail."""


class H3Director(Director):
    """ComfyUI MiniMax H3 image-to-video director."""

    def __init__(
        self,
        output_dir: Path,
        comfy_url: str = DEFAULT_COMFY_URL,
        workflow_path: Path = DEFAULT_WORKFLOW_PATH,
        motion_presets: dict[str, str] | None = None,
        subject_template: str = "A studio product shot of the subject in the source image.",
        request_func: Any = requests.request,
    ) -> None:
        """Initialize H3 director.

        Args:
            output_dir: Directory for rendered outputs.
            comfy_url: ComfyUI API base URL.
            workflow_path: Path to the workflow JSON template.
            motion_presets: Optional motion preset prompt fragments.
            subject_template: Base subject description for prompts.
            request_func: Injectable HTTP request function.
        """
        self.output_dir = output_dir
        self.comfy_url = comfy_url.rstrip("/")
        self.workflow_path = workflow_path
        self.motion_presets = motion_presets or DEFAULT_MOTION_PRESETS
        self.subject_template = subject_template
        self.client_id = str(uuid.uuid4())
        self._request_func = request_func
        self._workflow_template = self._load_workflow_template()

    def _load_workflow_template(self) -> dict[str, Any]:
        """Load the ComfyUI workflow JSON template."""
        if not self.workflow_path.exists():
            raise FileNotFoundError(f"Workflow not found: {self.workflow_path}")
        return json.loads(self.workflow_path.read_text(encoding="utf-8"))

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
            raise H3DirectorError(f"ComfyUI {method} {path} failed: {response.status_code} {response.text}")
        return response

    def _upload_image(self, image_path: Path) -> str:
        """Upload source image to ComfyUI input folder."""
        with image_path.open("rb") as handle:
            files = {"image": (image_path.name, handle, "image/png")}
            data = {"subfolder": "", "type": "input"}
            response = self._http("POST", "/upload/image", files=files, data=data, timeout=60)
        payload = response.json()
        return payload.get("name", image_path.name)

    def _build_prompt(self, output_spec: OutputSpec) -> str:
        """Build the MiniMax H3 prompt text from motion preset."""
        motion = self.motion_presets.get(output_spec.motion, output_spec.motion)
        duration = output_spec.duration_sec
        return (
            f"{self.subject_template}\n"
            f"{motion}\n"
            f"Duration target: {duration}s at 24fps. No dialogue."
        )

    def _length_frames(self, duration_sec: int) -> int:
        """Convert duration seconds to frame count at 24fps."""
        return max(24, int(duration_sec * FPS))

    def prepare(self, source_image: Path, output_spec: OutputSpec) -> JobSpec:
        """Prepare a ComfyUI job from source image and output spec."""
        if output_spec.format not in FORMAT_MAP:
            raise H3DirectorError(f"Unsupported format: {output_spec.format}")

        fmt = FORMAT_MAP[output_spec.format]
        uploaded_name = self._upload_image(source_image)
        workflow = copy.deepcopy(self._workflow_template)

        workflow["5"]["inputs"]["image"] = uploaded_name
        workflow["6"]["inputs"]["aspect_ratio"] = fmt["aspect_ratio"]
        workflow["6"]["inputs"]["megapixels"] = output_spec.megapixels

        prompt_text = self._build_prompt(output_spec)
        length_frames = self._length_frames(output_spec.duration_sec)

        workflow["7"]["inputs"]["prompt"] = prompt_text
        workflow["7"]["inputs"]["width"] = fmt["width"]
        workflow["7"]["inputs"]["height"] = fmt["height"]
        workflow["7"]["inputs"]["length"] = length_frames
        workflow["8"]["inputs"]["noise_seed"] = random.randint(1, 2**31 - 1)

        stem = source_image.stem
        prefix = f"video/{stem}_{output_spec.format.replace(':', 'x')}"
        workflow["16"]["inputs"]["filename_prefix"] = prefix

        self.output_dir.mkdir(parents=True, exist_ok=True)
        output_path = self.output_dir / f"{stem}_{output_spec.format.replace(':', 'x')}.mp4"

        return JobSpec(
            source_image=source_image,
            output_spec=output_spec,
            workflow=workflow,
            output_path=output_path,
            prompt_text=prompt_text,
            width=fmt["width"],
            height=fmt["height"],
            length_frames=length_frames,
            extra={"uploaded_name": uploaded_name, "filename_prefix": prefix},
        )

    def submit(self, job: JobSpec) -> str:
        """Submit workflow to ComfyUI /prompt endpoint."""
        payload = {"prompt": job.workflow, "client_id": self.client_id}
        response = self._http("POST", "/prompt", json=payload, timeout=60)
        data = response.json()
        prompt_id = data.get("prompt_id")
        if not prompt_id:
            raise H3DirectorError(f"ComfyUI did not return prompt_id: {data}")
        logger.info("Submitted job prompt_id={}", prompt_id)
        return prompt_id

    def _extract_video_path(self, history_entry: dict[str, Any], job: JobSpec) -> Path:
        """Extract rendered video path from ComfyUI history output."""
        outputs = history_entry.get("outputs", {})
        for node_output in outputs.values():
            videos = node_output.get("videos", [])
            if videos:
                video_info = videos[0]
                filename = video_info["filename"]
                subfolder = video_info.get("subfolder", "")
                folder_type = video_info.get("type", "output")
                params = {
                    "filename": filename,
                    "subfolder": subfolder,
                    "type": folder_type,
                }
                response = self._http("GET", "/view", params=params, timeout=120)
                job.output_path.parent.mkdir(parents=True, exist_ok=True)
                job.output_path.write_bytes(response.content)
                return job.output_path

        status = history_entry.get("status", {})
        if status.get("status_str") == "error":
            messages = status.get("messages", [])
            raise H3DirectorError(f"ComfyUI render failed: {messages}")

        raise H3DirectorError(f"No video output in history for job {job.output_path.name}")

    def poll_until_done(self, prompt_id: str, timeout_sec: int) -> VideoResult:
        """Poll ComfyUI history until render completes."""
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
                    raise H3DirectorError(f"Render failed: {messages}")

                # Success when outputs present or status success
                if entry.get("outputs") or status_str == "success":
                    render_sec = time.time() - started
                    # Need job for output path - store minimal job ref via extra lookup
                    # Caller should pass job separately; we reconstruct from history
                    video_path = self._download_first_video(entry)
                    duration_sec = self._probe_duration(video_path)
                    return VideoResult(
                        video_path=video_path,
                        prompt_id=prompt_id,
                        duration_sec=duration_sec,
                        render_sec=render_sec,
                        raw_outputs=entry,
                    )

            time.sleep(2.0)

        raise H3DirectorError(f"Timeout waiting for prompt_id={prompt_id} after {timeout_sec}s")

    def poll_until_done_with_job(
        self,
        prompt_id: str,
        timeout_sec: int,
        job: JobSpec,
    ) -> VideoResult:
        """Poll until done and write output to job.output_path."""
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
                    raise H3DirectorError(f"Render failed: {messages}")

                if entry.get("outputs") or status_str == "success":
                    render_sec = time.time() - started
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

        raise H3DirectorError(f"Timeout waiting for prompt_id={prompt_id} after {timeout_sec}s")

    def _download_first_video(self, entry: dict[str, Any]) -> Path:
        """Download first video from history to a temp path."""
        outputs = entry.get("outputs", {})
        for node_output in outputs.values():
            videos = node_output.get("videos", [])
            if videos:
                video_info = videos[0]
                filename = video_info["filename"]
                subfolder = video_info.get("subfolder", "")
                folder_type = video_info.get("type", "output")
                params = {
                    "filename": filename,
                    "subfolder": subfolder,
                    "type": folder_type,
                }
                response = self._http("GET", "/view", params=params, timeout=120)
                out = self.output_dir / filename
                out.parent.mkdir(parents=True, exist_ok=True)
                out.write_bytes(response.content)
                return out
        raise H3DirectorError("No video in history outputs")

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
        """Upscale video to target format dimensions using ffmpeg Lanczos."""
        if target_format not in FORMAT_MAP:
            raise H3DirectorError(f"Unknown target format: {target_format}")

        target_w = FORMAT_MAP[target_format]["width"]
        target_h = FORMAT_MAP[target_format]["height"]
        upscaled_path = video_path.with_name(f"{video_path.stem}_upscaled.mp4")

        vf = f"scale={target_w}:{target_h}:force_original_aspect_ratio=decrease"
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
            raise H3DirectorError(f"ffmpeg upscale failed: {proc.stderr}")

        drift = self._verify_ar_drift(upscaled_path, target_w / target_h)
        if drift >= 0.01:
            logger.warning("AR drift after upscale: {:.4f}", drift)

        return upscaled_path

    def _verify_ar_drift(self, video_path: Path, target_ar: float) -> float:
        """Verify aspect ratio drift after upscale."""
        proc = subprocess.run(
            [
                "ffprobe",
                "-v",
                "quiet",
                "-select_streams",
                "v:0",
                "-show_entries",
                "stream=width,height",
                "-of",
                "csv=p=0:s=x",
                str(video_path),
            ],
            check=False,
            capture_output=True,
            text=True,
            timeout=10,
        )
        if proc.returncode != 0:
            return 0.0
        parts = proc.stdout.strip().split("x")
        if len(parts) != 2:
            return 0.0
        w, h = int(parts[0]), int(parts[1])
        actual_ar = w / h if h else 0.0
        if target_ar == 0.0:
            return 0.0
        return abs(actual_ar - target_ar) / target_ar
