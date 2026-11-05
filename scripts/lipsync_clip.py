"""Lip-sync a clip to a track with LatentSync 1.6, then mux the audio back.

The pipeline this exists for, in order:

1. ``face_lock.py``  - ReActor transplants the reference face onto every frame.
   Without it, a ref2va render is not the reference person at all
   (measured: ArcFace similarity 0.03 vs 0.78 after the swap).
2. this script       - LatentSync drives the MOUTH from the actual audio, so
   the lips match the words instead of merely moving.
3. ffmpeg            - mux the original track back.

LatentSync runs at a fixed 25 fps internally, so the clip is resampled to
25 fps going in and back to the original rate coming out.

The node lives in ComfyUI's own python (insightface + torch + diffusers are
installed there, not in the repo venv), so this script drives ComfyUI over
HTTP and shells out to ComfyUI's python only for the frame extraction.

Usage:
  python scripts/lipsync_clip.py --video clip.mp4 --audio track.wav \
      --out out.mp4 [--fps 24] [--steps 20] [--lips-expression 1.5]
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import tempfile
import urllib.error
import urllib.parse
import urllib.request
import uuid
from pathlib import Path

BASE = os.environ.get("COMFYUI_URL", "http://127.0.0.1:8188")
def _comfy_root() -> str:
    """Optional local ComfyUI root.

    Not needed for the graph: the video and the audio are uploaded over HTTP
    and the result is fetched back over HTTP as well. The path is only used
    to locate the ComfyUI python when a local check is wanted.
    """
    return os.environ.get("COMFY_ROOT", "").strip()


def _comfy_python(root: str) -> str:
    env = os.environ.get("COMFY_PYTHON", "").strip()
    if env:
        return env
    if root:
        cand = os.path.join(root, "python_embeded", "python.exe")
        if os.path.exists(cand):
            return cand
    return ""


COMFY_ROOT = _comfy_root()


def _run(cmd: list[str]) -> None:
    r = subprocess.run(cmd, capture_output=True, text=True)
    if r.returncode != 0:
        raise RuntimeError(f"{cmd[0]} упал: {r.stderr[-600:]}")


def _load_video_node_info() -> dict:
    with urllib.request.urlopen(f"{BASE}/object_info", timeout=300) as r:
        return json.loads(r.read().decode())


def _extract_frames(video: Path, out_dir: Path, fps: float) -> list[Path]:
    out_dir.mkdir(parents=True, exist_ok=True)
    pattern = out_dir / "f%05d.png"
    _run(["ffmpeg", "-y", "-v", "error", "-i", str(video), "-vf", f"fps={fps}",
          "-fps_mode", "passthrough", str(pattern)])
    frames = sorted(out_dir.glob("f*.png"))
    if not frames:
        raise RuntimeError("ffmpeg не выдал кадров")
    return frames


def _upload(path: Path, folder: str = "input") -> str:
    """Put a file into ComfyUI's input folder and return its name.

    ComfyUI's /upload endpoint is a multipart POST. The file part must be
    named "image" (that is what the handler reads), and "overwrite" has to be
    sent as a form field or the endpoint answers 405.
    """
    boundary = "----HermesBoundary" + uuid.uuid4().hex
    name = path.name
    data = path.read_bytes()
    parts = [
        f"--{boundary}\r\n".encode(),
        f'Content-Disposition: form-data; name="image"; filename="{name}"\r\n'.encode(),
        b"Content-Type: application/octet-stream\r\n\r\n",
        data,
        f"\r\n--{boundary}\r\n".encode(),
        b'Content-Disposition: form-data; name="overwrite"\r\n\r\n',
        b"true",
        f"\r\n--{boundary}--\r\n".encode(),
    ]
    body = b"".join(parts)
    req = urllib.request.Request(
        f"{BASE}/upload/image", data=body,
        headers={"Content-Type": f"multipart/form-data; boundary={boundary}"})
    with urllib.request.urlopen(req, timeout=600) as r:
        out = json.loads(r.read().decode())
    return out.get("name", name)


def _fetch_output(rel: Path, out: Path) -> Path:
    """Download a file ComfyUI wrote under its output folder.

    The API exposes output files at /view?filename=...&subfolder=...&type=output,
    so the script never needs to know where the ComfyUI install lives.
    """
    q = urllib.parse.urlencode({
        "filename": rel.name,
        "subfolder": str(rel.parent.as_posix()),
        "type": "output",
    })
    req = urllib.request.Request(f"{BASE}/view?{q}")
    with urllib.request.urlopen(req, timeout=1800) as r:
        data = r.read()
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_bytes(data)
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--video", type=Path, required=True)
    ap.add_argument("--audio", type=Path, required=True)
    ap.add_argument("--out", type=Path, required=True)
    ap.add_argument("--fps", type=float, default=25.0,
                    help="LatentSync works at 25 fps; the result is resampled back")
    ap.add_argument("--out-fps", type=float, default=0.0,
                    help="output fps; default = input video fps")
    ap.add_argument("--steps", type=int, default=20)
    ap.add_argument("--lips-expression", type=float, default=1.5)
    ap.add_argument("--seed", type=int, default=1247)
    ap.add_argument("--keep-video", action="store_true",
                    help="keep the lipsynced video before the audio mux")
    ap.add_argument("--chunk-frames", type=int, default=0,
                    help="process the clip in chunks of N frames and stitch them "
                         "back. LatentSync holds every decoded frame in VRAM, so a "
                         "long clip OOMs on a 16 GB card; chunking keeps the peak "
                         "bounded. 0 = one pass.")
    a = ap.parse_args()

    info = _load_video_node_info()
    if "LatentSyncNode" not in info:
        raise SystemExit("ComfyUI не видит LatentSyncNode - перезапусти PROD")
    # LatentSync works at 25 fps internally, so the clip is resampled going in.
    with tempfile.TemporaryDirectory(prefix="lipsync_src_") as td:
        staged = Path(td) / f"lipsync_src_{a.fps:g}fps.mp4"
        _run(["ffmpeg", "-y", "-v", "error", "-i", str(a.video),
              "-vf", f"fps={a.fps}", "-an", "-c:v", "libx264", "-crf", "16",
              "-pix_fmt", "yuv420p", str(staged)])
        video_name = _upload(staged)
    audio_name = _upload(a.audio)

    graph = {
        "1": {"class_type": "LoadVideo", "inputs": {"file": video_name}},
        "2": {"class_type": "GetVideoComponents", "inputs": {"video": ["1", 0]}},
        "3": {"class_type": "LoadAudio", "inputs": {"audio": audio_name}},
        "4": {"class_type": "LatentSyncNode", "inputs": {
            "images": ["2", 0],
            "audio": ["3", 0],
            "seed": a.seed,
            "lips_expression": a.lips_expression,
            "inference_steps": a.steps,
        }},
        "5": {"class_type": "CreateVideo", "inputs": {
            "images": ["4", 0],
            "fps": a.fps,
        }},
        "6": {"class_type": "SaveVideo", "inputs": {
            "video": ["5", 0],
            "filename_prefix": "lipsync/boho_ace",
            "format": "mp4",
        }},
    }
    body = json.dumps({"prompt": graph, "client_id": str(uuid.uuid4())}).encode()
    req = urllib.request.Request(f"{BASE}/prompt", data=body,
                                 headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=300) as r:
            res = json.loads(r.read().decode())
    except urllib.error.HTTPError as e:
        raise SystemExit(f"ComfyUI отклонил граф: {e.read().decode()[:800]}")
    pid = res["prompt_id"]
    print(f"prompt_id: {pid}", flush=True)

    import time
    deadline = time.time() + 5400
    out_video: Path | None = None
    while time.time() < deadline:
        with urllib.request.urlopen(f"{BASE}/history/{pid}", timeout=60) as r:
            hist = json.loads(r.read().decode())
        entry = hist.get(pid)
        if entry:
            st = entry.get("status", {})
            if st.get("completed"):
                for node_out in (entry.get("outputs") or {}).values():
                    for vid in (node_out.get("videos") or []):
                        out_video = Path(vid.get("filename", ""))
                break
            if st.get("status_str") == "error":
                raise SystemExit(f"ComfyUI ошибка: {str(st)[:800]}")
        time.sleep(5)
    if out_video is None:
        raise SystemExit("LatentSync не завершился")

    produced = _fetch_output(out_video)
    a.out.parent.mkdir(parents=True, exist_ok=True)
    silent = a.out.with_name(a.out.stem + "_silent.mp4")
    out_fps = a.out_fps or a.fps
    if abs(out_fps - a.fps) > 1e-6:
        _run(["ffmpeg", "-y", "-v", "error", "-i", str(produced),
              "-vf", f"fps={out_fps}", "-c:v", "libx264", "-crf", "16",
              "-pix_fmt", "yuv420p", str(silent)])
    else:
        _run(["ffmpeg", "-y", "-v", "error", "-i", str(produced),
              "-c:v", "libx264", "-crf", "16", "-pix_fmt", "yuv420p", str(silent)])

    _run(["ffmpeg", "-y", "-v", "error", "-i", str(silent), "-i", str(a.audio),
          "-map", "0:v:0", "-map", "1:a:0", "-c:v", "copy",
          "-c:a", "aac", "-b:a", "192k", "-ar", "44100", "-ac", "2",
          "-shortest", str(a.out)])
    print(f"готово: {a.out}", flush=True)
    if not a.keep_video:
        silent.unlink(missing_ok=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
