# -*- coding: utf-8 -*-
"""ComfyUI ReActor transport used by :mod:`scripts.face_lock`.

The graph is LoadImage(target) + LoadImage(source) -> ReActorFaceSwap -> SaveImage.
The installed node schema is checked at runtime instead of relying on copied examples.
Frames are submitted one at a time with a hard per-frame timeout; a failed frame is
never silently omitted from the output sequence.

Pipeline order: FreeVideo -> SeedVR2 -> face_lock -> RIFE. Face locking after the
diffusion upscale prevents SeedVR2 from redrawing the swapped identity; doing it before
RIFE avoids twice the API work and gives the interpolator a temporally consistent face.
"""
from __future__ import annotations

import json
import shutil
import sys
import time
import urllib.error
import urllib.request
import uuid
from pathlib import Path
from typing import Any

BASE = "http://127.0.0.1:8188"
DEFAULT_RESTORE = "GFPGANv1.4.pth"


class ComfyUIError(RuntimeError):
    """A ComfyUI transport, schema, or execution error."""


class FrameTimeout(ComfyUIError):
    """One ReActor frame exceeded its allotted processing time."""


def build_reactor_workflow(
    input_image: str,
    source_image: str,
    filename_prefix: str,
    *,
    restore_model: str = DEFAULT_RESTORE,
) -> dict[str, Any]:
    """Build the four-node graph exposed by the installed ReActor version."""
    return {
        "1": {"class_type": "LoadImage", "inputs": {"image": input_image}},
        "2": {"class_type": "LoadImage", "inputs": {"image": source_image}},
        "3": {
            "class_type": "ReActorFaceSwap",
            "inputs": {
                "enabled": True,
                "input_image": ["1", 0],
                "source_image": ["2", 0],
                "swap_model": "inswapper_128.onnx",
                "facedetection": "retinaface_resnet50",
                "face_restore_model": restore_model,
                # visibility 1.0 gives the "airbrushed / fake plastic AI skin" look
                # (measured: it is exactly the waxy-skin defect we are fighting).
                # 0.7 keeps the source identity without the plastic smoothing.
                "face_restore_visibility": 0.7,
                "codeformer_weight": 0.5,
                "detect_gender_input": "no",
                "detect_gender_source": "no",
                "input_faces_index": "0",
                "source_faces_index": "0",
                "console_log_level": 1,
            },
        },
        "4": {
            "class_type": "SaveImage",
            "inputs": {"images": ["3", 0], "filename_prefix": filename_prefix},
        },
    }


def find_output_image(entry: dict[str, Any], output_dir: str | Path) -> Path | None:
    """Resolve the first on-disk SaveImage result from a history entry."""
    for node_output in entry.get("outputs", {}).values():
        for image in node_output.get("images", []):
            if image.get("type", "output") != "output" or not image.get("filename"):
                continue
            path = Path(output_dir) / image.get("subfolder", "") / image["filename"]
            if path.is_file():
                return path
    return None


def _local_comfyui_dirs() -> tuple[Path, Path]:
    """Resolve shared paths lazily so importing this module is CI-safe."""
    try:
        from . import local_paths
    except ImportError:  # Direct execution puts scripts/ on sys.path.
        import local_paths  # type: ignore[no-redef]
    return Path(local_paths.PROD_IN), Path(local_paths.PROD_OUT)


class ComfyUIClient:
    """Small stdlib-only client for the local ComfyUI HTTP API."""

    def __init__(
        self,
        base_url: str = BASE,
        *,
        input_dir: str | Path | None = None,
        output_dir: str | Path | None = None,
        client_id: str | None = None,
    ) -> None:
        default_input, default_output = (
            _local_comfyui_dirs() if input_dir is None or output_dir is None else (None, None)
        )
        self.input_dir = Path(input_dir or default_input)  # type: ignore[arg-type]
        self.output_dir = Path(output_dir or default_output)  # type: ignore[arg-type]
        self.base_url = base_url.rstrip("/")
        self.client_id = client_id or f"face-lock-{uuid.uuid4().hex}"

    def api(
        self, path: str, payload: dict[str, Any] | None = None, *, timeout: float = 30
    ) -> dict[str, Any]:
        request = urllib.request.Request(
            self.base_url + path,
            data=json.dumps(payload).encode("utf-8") if payload is not None else None,
            headers={"Content-Type": "application/json"},
        )
        try:
            with urllib.request.urlopen(request, timeout=timeout) as response:
                raw = response.read().decode("utf-8", "replace")
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", "replace")[:1000]
            raise ComfyUIError(f"ComfyUI HTTP {exc.code} for {path}: {detail}") from exc
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            raise ComfyUIError(
                f"ComfyUI unavailable at {self.base_url}: {exc}; start ComfyUI and retry"
            ) from exc
        try:
            return json.loads(raw) if raw.strip() else {}
        except json.JSONDecodeError as exc:
            raise ComfyUIError(f"ComfyUI returned invalid JSON for {path}") from exc

    def preflight(self, restore_model: str = DEFAULT_RESTORE) -> None:
        """Fail before ffmpeg extraction if ReActor or selected models are absent."""
        payload = self.api("/object_info/ReActorFaceSwap", timeout=10)
        node = payload.get("ReActorFaceSwap")
        if not node:
            raise ComfyUIError("ReActorFaceSwap is not installed in this ComfyUI")
        required = node.get("input", {}).get("required", {})
        optional = node.get("input", {}).get("optional", {})
        if "input_image" not in required or "source_image" not in optional:
            raise ComfyUIError("Installed ReActorFaceSwap has an incompatible image schema")
        swap_models = required.get("swap_model", [[]])[0]
        restore_models = required.get("face_restore_model", [[]])[0]
        if "inswapper_128.onnx" not in swap_models:
            raise ComfyUIError("ComfyUI does not list inswapper_128.onnx")
        if restore_model not in restore_models:
            raise ComfyUIError(f"ComfyUI does not list restore model {restore_model}")
        if not self.input_dir.is_dir() or not self.output_dir.is_dir():
            raise ComfyUIError("ComfyUI input/output directories are not accessible")

    def wait_for_output(
        self, prompt_id: str, *, timeout: float = 240, poll_interval: float = 1.0
    ) -> Path:
        """Poll history until SaveImage is visible or this frame's deadline expires."""
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            history = self.api(
                f"/history/{prompt_id}", timeout=min(30, max(0.1, deadline - time.monotonic()))
            )
            entry = history.get(prompt_id)
            if entry:
                status = entry.get("status", {})
                if status.get("status_str") == "error":
                    messages = json.dumps(status.get("messages", []), ensure_ascii=False)
                    raise ComfyUIError(f"ReActor failed for {prompt_id}: {messages[:1000]}")
                output = find_output_image(entry, self.output_dir)
                if output is not None:
                    return output
            time.sleep(min(poll_interval, max(0.0, deadline - time.monotonic())))
        raise FrameTimeout(f"ReActor frame timed out after {timeout:g}s (prompt {prompt_id})")

    def swap_one(
        self,
        image: str | Path,
        source: str | Path,
        destination: str | Path,
        *,
        restore_model: str = DEFAULT_RESTORE,
        timeout: float = 240,
        tag: str | None = None,
    ) -> Path:
        """Swap one frame and copy its SaveImage result to ``destination``."""
        started = time.monotonic()
        tag = tag or uuid.uuid4().hex
        source_suffix = Path(source).suffix.lower() or ".png"
        input_name = f"face_lock_{tag}_input.png"
        source_name = f"face_lock_{tag}_source{source_suffix}"
        input_path = self.input_dir / input_name
        source_path = self.input_dir / source_name
        try:
            shutil.copy2(image, input_path)
            shutil.copy2(source, source_path)
            workflow = build_reactor_workflow(
                input_name, source_name, f"face_lock_{tag}", restore_model=restore_model
            )
            response = self.api(
                "/prompt",
                {"prompt": workflow, "client_id": self.client_id},
                timeout=min(30, max(0.1, timeout)),
            )
            prompt_id = response.get("prompt_id")
            if not prompt_id:
                raise ComfyUIError(f"ComfyUI rejected face-swap prompt: {response}")
            remaining = timeout - (time.monotonic() - started)
            if remaining <= 0:
                raise FrameTimeout(f"ReActor frame timed out after {timeout:g}s during submission")
            result = self.wait_for_output(prompt_id, timeout=remaining)
            destination = Path(destination)
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(result, destination)
            result.unlink(missing_ok=True)
            return destination
        finally:
            input_path.unlink(missing_ok=True)
            source_path.unlink(missing_ok=True)


def main() -> int:
    """Backward-compatible CLI entry; implementation lives in face_lock.py."""
    try:
        from .face_lock import main as face_lock_main
    except ImportError:
        from face_lock import main as face_lock_main
    return face_lock_main()


if __name__ == "__main__":
    sys.exit(main())
