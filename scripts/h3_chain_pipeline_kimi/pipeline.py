#!/usr/bin/env python
"""Resumable long-video H3 pipeline based on last-frame chaining.

The module deliberately has no ComfyUI or local-path imports at module load:
``--plan`` is an offline operation.  Rendering is delegated to the existing
``scripts/h3_i2v_run.py`` process, one process per chunk attempt.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import shutil
import statistics
import subprocess
import sys
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Sequence

FPS = 24.0
CROSSFADE_SECONDS = 0.04
SCHEMA_VERSION = 1
SAMPLE_FRAMES = 24
SEED_CHUNK_STRIDE = 1_000_000
RUNNER = Path(__file__).resolve().parents[1] / "h3_i2v_run.py"
REPO_ROOT = Path(__file__).resolve().parents[2]


@dataclass(frozen=True)
class ChunkPlan:
    index: int
    render_frames: int
    kept_frames: int
    output_start: int
    seed: int

    @property
    def output_end(self) -> int:
        return self.output_start + self.kept_frames


@dataclass(frozen=True)
class ChainConfig:
    image: str
    prompt_file: Path
    audio: Path
    output: Path
    chunk_frames: int
    total_frames: int
    width: int
    height: int
    steps: int
    seed_base: int
    max_retries: int
    max_face_drift: float
    min_texture: float
    max_texture_drop: float
    state: Path
    server: str

    @property
    def work_dir(self) -> Path:
        return self.output.parent / f".{self.output.stem}.h3-chain"

    @property
    def report_path(self) -> Path:
        return self.output.with_suffix(self.output.suffix + ".report.json")


@dataclass(frozen=True)
class GateResult:
    accepted: bool
    reasons: list[str]
    penalty: float


def seed_for(seed_base: int, chunk_index: int, attempt: int) -> int:
    """Stable, distinct seeds for the initial render and every retry."""
    seed = seed_base + chunk_index * SEED_CHUNK_STRIDE + attempt
    if not 0 <= seed <= 2**63 - 1:
        raise ValueError("derived seed is outside the renderer's integer range")
    return seed


def build_chunk_plan(total_frames: int, chunk_frames: int, seed_base: int) -> list[ChunkPlan]:
    """Split an exact output length into H3-valid ``17*k+5`` render lengths.

    Every chunk after the first contributes one fewer frame because its first
    frame duplicates the preceding chunk's last frame.  When the requested
    total cannot be represented exactly by H3 lengths, only the tail of the
    final chunk is trimmed; no boundary frame is ever duplicated in output.
    """
    if total_frames < 2:
        raise ValueError("total frames must be at least 2")
    if chunk_frames < 5 or (chunk_frames - 5) % 17:
        raise ValueError("chunk frames must be 5 + 17*k (the H3 length constraint)")
    if seed_base < 0:
        raise ValueError("seed base must be non-negative")

    count = max(1, math.ceil((total_frames - 1) / (chunk_frames - 1)))
    tail_trim = (4 * count + 1 - total_frames) % 17
    raw_total = total_frames + count - 1 + tail_trim
    units_total = (raw_total - 5 * count) // 17
    max_units = (chunk_frames - 5) // 17

    lengths: list[int] = []
    remaining = units_total
    for _ in range(count):
        units = min(max_units, remaining)
        lengths.append(17 * units + 5)
        remaining -= units
    if remaining or any(length > chunk_frames for length in lengths):
        raise ValueError("could not fit the requested total into H3 chunk lengths")

    kept = [length if i == 0 else length - 1 for i, length in enumerate(lengths)]
    kept[-1] -= tail_trim
    if kept[-1] < 1:
        raise ValueError("tail trim would empty the final chunk")

    plan: list[ChunkPlan] = []
    start = 0
    for index, (render, keep) in enumerate(zip(lengths, kept)):
        plan.append(
            ChunkPlan(
                index=index,
                render_frames=render,
                kept_frames=keep,
                output_start=start,
                seed=seed_for(seed_base, index, 0),
            )
        )
        start += keep
    if start != total_frames:
        raise AssertionError("internal chunk-plan length mismatch")
    return plan


def format_plan(plan: Sequence[ChunkPlan], total_frames: int) -> str:
    lines = [
        "chunk | render | kept | output start | output end | initial seed | anchor",
        "------+--------+------+--------------+------------+--------------+-------",
    ]
    for chunk in plan:
        anchor = "input image" if chunk.index == 0 else f"last frame of chunk {chunk.index - 1}"
        lines.append(
            f"{chunk.index:5d} | {chunk.render_frames:6d} | {chunk.kept_frames:4d} | "
            f"{chunk.output_start:12d} | {chunk.output_end:10d} | "
            f"{chunk.seed:12d} | {anchor}"
        )
    rendered = sum(c.render_frames for c in plan)
    raw_kept = rendered - (len(plan) - 1)
    lines.append(
        f"total: {len(plan)} chunks, {total_frames} output frames at {FPS:g} fps; "
        f"boundary duplicates removed: {len(plan) - 1}; final tail trim: {raw_kept - total_frames}"
    )
    lines.append("PLAN ONLY: no state files, ffmpeg, renderer, or ComfyUI calls were made.")
    return "\n".join(lines)


def _finite(value: Any) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)


def evaluate_gate(metrics: dict[str, Any], *, max_face_drift: float, texture_floor: float) -> GateResult:
    """Return all gate failures and a normalized penalty for best-effort choice."""
    reasons: list[str] = []
    penalty = 0.0
    drift = metrics.get("face_drift")
    pore = metrics.get("pore")
    frames = metrics.get("frames")

    if not _finite(drift):
        reasons.append("face_drift is missing or non-finite")
        penalty += 1_000_000.0
        drift_value = float("inf")
    else:
        drift_value = float(drift)
        if drift_value > max_face_drift:
            reasons.append(f"face_drift {drift_value:.6g} exceeds {max_face_drift:.6g}")
            penalty += drift_value / max(max_face_drift, 1e-12) - 1.0

    if not _finite(pore):
        reasons.append("texture pore energy is missing or non-finite")
        penalty += 1_000_000.0
        pore_value = 0.0
    else:
        pore_value = float(pore)
        if pore_value < texture_floor:
            reasons.append(f"texture pore energy {pore_value:.6g} is below floor {texture_floor:.6g}")
            penalty += texture_floor / max(pore_value, 1e-12) - 1.0

    if not isinstance(frames, int) or frames < 2:
        reasons.append("fewer than two sampled frames")
        penalty += 1_000_000.0
    return GateResult(accepted=not reasons, reasons=reasons, penalty=penalty)


def choose_best_attempt(
    attempts: Sequence[dict[str, Any]], *, max_face_drift: float, texture_floor: float
) -> dict[str, Any]:
    """Choose the least gate violation, then lower drift and higher texture."""
    candidates = [a for a in attempts if isinstance(a.get("metrics"), dict)]
    if not candidates:
        raise ValueError("no measured attempt is available")

    def key(attempt: dict[str, Any]) -> tuple[float, float, float, int]:
        metrics = attempt["metrics"]
        result = evaluate_gate(
            metrics, max_face_drift=max_face_drift, texture_floor=texture_floor
        )
        drift = metrics.get("face_drift")
        pore = metrics.get("pore")
        return (
            result.penalty,
            float(drift) if _finite(drift) else float("inf"),
            -float(pore) if _finite(pore) else 0.0,
            int(attempt["attempt"]),
        )

    return min(candidates, key=key)


def build_renderer_argv(
    *,
    image_name: str,
    output: Path,
    prompt_file: Path,
    audio: Path,
    prefix: str,
    width: int,
    height: int,
    render_frames: int,
    steps: int,
    seed: int,
    audio_offset_frames: int,
    server: str,
) -> list[str]:
    """Build a renderer command; LoadImage receives a basename, never a path."""
    if Path(image_name).name != image_name:
        raise ValueError("renderer image must be a filename inside ComfyUI input")
    argv = [
        sys.executable,
        "-u",
        str(RUNNER),
        "--image",
        image_name,
        "--width",
        str(width),
        "--height",
        str(height),
        "--length",
        str(render_frames),
        "--seed",
        str(seed),
        "--steps",
        str(steps),
        "--prompt-file",
        str(prompt_file),
        "--prefix",
        prefix,
        "--audio",
        str(audio),
        "--audio-offset",
        str(audio_offset_frames),
        "--output",
        str(output),
        "--wait",
        "5400",
    ]
    if server:
        argv += ["--server", server]
    return argv


def build_audio_filter(kept_frames: Sequence[int], fps: float = FPS) -> str:
    """Create 40 ms qsin (equal-power) joins for the exact retained segments."""
    if not kept_frames or any(frames < 1 for frames in kept_frames):
        raise ValueError("audio filter needs positive retained frame counts")
    filters: list[str] = []
    for index, frames in enumerate(kept_frames):
        duration = frames / fps
        filters.append(
            f"[{index}:a]atrim=end={duration:.6f},asetpts=PTS-STARTPTS[s{index}]"
        )
    current = "[s0]"
    for index in range(1, len(kept_frames)):
        output = f"[x{index}]"
        filters.append(
            f"{current}[s{index}]acrossfade=d={CROSSFADE_SECONDS:.2f}:c1=qsin:c2=qsin{output}"
        )
        current = output
    total_duration = sum(kept_frames) / fps
    filters.append(
        f"{current}apad=whole_dur={total_duration:.6f},"
        f"atrim=end={total_duration:.6f}[aout]"
    )
    return ";".join(filters)


def _json_safe(value: Any) -> Any:
    if isinstance(value, dict):
        return {str(key): _json_safe(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_safe(item) for item in value]
    if isinstance(value, Path):
        return str(value)
    if isinstance(value, float) and not math.isfinite(value):
        return None
    return value


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".tmp")
    temporary.write_text(
        json.dumps(_json_safe(payload), ensure_ascii=False, indent=2, allow_nan=False) + "\n",
        encoding="utf-8",
        newline="\n",
    )
    os.replace(temporary, path)


def save_state(path: Path, state: dict[str, Any]) -> None:
    _write_json(path, state)


def load_state(path: Path, *, expected_fingerprint: str) -> dict[str, Any]:
    try:
        state = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"cannot read state file {path}: {exc}") from exc
    if state.get("schema_version") != SCHEMA_VERSION:
        raise ValueError("unsupported state schema version")
    if state.get("fingerprint") != expected_fingerprint:
        raise ValueError("state fingerprint does not match this job; use a new state file")
    if not isinstance(state.get("chunks"), list):
        raise ValueError("state has no chunk list")
    return state


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _run_command(argv: Sequence[str], *, timeout: int = 1800) -> subprocess.CompletedProcess[str]:
    process = subprocess.run(
        list(argv), capture_output=True, text=True, check=False, timeout=timeout
    )
    if process.returncode:
        tail = ((process.stdout or "") + (process.stderr or ""))[-3000:]
        raise RuntimeError(f"command failed ({process.returncode}): {argv[0]}\n{tail}")
    return process


def probe_video(path: Path) -> dict[str, Any]:
    process = _run_command(
        [
            "ffprobe",
            "-v",
            "error",
            "-count_frames",
            "-select_streams",
            "v:0",
            "-show_entries",
            "stream=width,height,nb_read_frames,r_frame_rate",
            "-of",
            "json",
            str(path),
        ],
        timeout=300,
    )
    stream = json.loads(process.stdout)["streams"][0]
    return {
        "frames": int(stream["nb_read_frames"]),
        "width": int(stream["width"]),
        "height": int(stream["height"]),
        "fps": stream["r_frame_rate"],
    }


def extract_frame(video: Path, output: Path, frame_index: int) -> Path:
    output.parent.mkdir(parents=True, exist_ok=True)
    _run_command(
        [
            "ffmpeg",
            "-y",
            "-v",
            "error",
            "-i",
            str(video),
            "-vf",
            f"select=eq(n\\,{frame_index})",
            "-frames:v",
            "1",
            "-fps_mode",
            "passthrough",
            str(output),
        ],
        timeout=600,
    )
    if not output.exists():
        raise RuntimeError(f"ffmpeg did not extract frame {frame_index} from {video}")
    return output


def extract_sample_frames(video: Path, output_dir: Path, samples: int = SAMPLE_FRAMES) -> list[Path]:
    """Uniformly sample a clip, always including its first and last frames."""
    info = probe_video(video)
    count = info["frames"]
    if count < 2:
        raise RuntimeError(f"chunk has fewer than two frames: {video}")
    sample_count = min(samples, count)
    indices = sorted({round(i * (count - 1) / (sample_count - 1)) for i in range(sample_count)})
    expression = "+".join(f"eq(n\\,{index})" for index in indices)
    output_dir.mkdir(parents=True, exist_ok=True)
    for old in output_dir.glob("frame_*.png"):
        old.unlink()
    _run_command(
        [
            "ffmpeg",
            "-y",
            "-v",
            "error",
            "-i",
            str(video),
            "-vf",
            f"select={expression}",
            "-fps_mode",
            "passthrough",
            str(output_dir / "frame_%05d.png"),
        ],
        timeout=900,
    )
    paths = sorted(output_dir.glob("frame_*.png"))
    if len(paths) != len(indices):
        raise RuntimeError(f"expected {len(indices)} sampled frames, got {len(paths)}")
    return paths


def _texture_measure(frame_paths: Sequence[Path]) -> dict[str, Any]:
    """Use the repository's canonical pore/drift implementation lazily."""
    scripts_dir = str(RUNNER.parent)
    if scripts_dir not in sys.path:
        sys.path.insert(0, scripts_dir)
    from compare_video_texture import measure  # type: ignore

    return measure([str(path) for path in frame_paths])


class ChainPipeline:
    def __init__(self, config: ChainConfig):
        self.config = config
        self.plan = build_chunk_plan(config.total_frames, config.chunk_frames, config.seed_base)
        self.job_id = ""
        self._initial_source: Path | None = None
        self._input_dir: Path | None = None
        self.state: dict[str, Any] = {}

    def _comfy_input_dir(self) -> Path:
        if self._input_dir is not None:
            return self._input_dir
        configured = os.environ.get("COMFYUI_PROD_DIR")
        if configured:
            root = Path(configured)
            comfy = root if root.name.lower() == "comfyui" else root / "ComfyUI"
            self._input_dir = comfy / "input"
        else:
            scripts_dir = str(RUNNER.parent)
            if scripts_dir not in sys.path:
                sys.path.insert(0, scripts_dir)
            import local_paths  # type: ignore

            self._input_dir = local_paths.PROD_IN
        return self._input_dir

    def _prepare_initial_image(self) -> str:
        supplied = Path(self.config.image)
        source = supplied if supplied.is_file() else self._comfy_input_dir() / supplied.name
        if not source.is_file():
            raise FileNotFoundError(f"initial image not found: {self.config.image}")
        self._initial_source = source
        identity = json.dumps(
            {
                "image_sha256": _sha256(source),
                "output": str(self.config.output),
                "total": self.config.total_frames,
                "seed": self.config.seed_base,
            },
            sort_keys=True,
        )
        self.job_id = hashlib.sha256(identity.encode("utf-8")).hexdigest()[:16]
        suffix = source.suffix.lower() if source.suffix else ".png"
        name = f"h3chain_{self.job_id}_anchor_000{suffix}"
        destination = self._comfy_input_dir() / name
        destination.parent.mkdir(parents=True, exist_ok=True)
        if source.resolve() != destination.resolve():
            shutil.copy2(source, destination)
        return name

    def _stage_anchor(self, local_path: Path, chunk_index: int) -> str:
        name = f"h3chain_{self.job_id}_anchor_{chunk_index + 1:03d}.png"
        destination = self._comfy_input_dir() / name
        shutil.copy2(local_path, destination)
        return name

    def _fingerprint(self) -> str:
        if self._initial_source is None:
            raise RuntimeError("initial image was not prepared")
        config = asdict(self.config)
        config["state"] = ""
        payload = {
            "schema": SCHEMA_VERSION,
            "config": config,
            "files": {
                "image": _sha256(self._initial_source),
                "prompt": _sha256(self.config.prompt_file),
                "audio": _sha256(self.config.audio),
            },
        }
        encoded = json.dumps(_json_safe(payload), sort_keys=True, ensure_ascii=False)
        return hashlib.sha256(encoded.encode("utf-8")).hexdigest()

    def _new_state(self, fingerprint: str) -> dict[str, Any]:
        return {
            "schema_version": SCHEMA_VERSION,
            "fingerprint": fingerprint,
            "job_id": self.job_id,
            "config": _json_safe(asdict(self.config)),
            "chunks": [
                {
                    "index": chunk.index,
                    "initial_seed": chunk.seed,
                    "render_frames": chunk.render_frames,
                    "kept_frames": chunk.kept_frames,
                    "output_start": chunk.output_start,
                    "status": "pending",
                    "attempts": [],
                    "selected_attempt": None,
                    "selected_seed": None,
                    "gate_passed": False,
                    "metrics": None,
                    "video": None,
                    "last_frame": None,
                }
                for chunk in self.plan
            ],
            "assembly": {"status": "pending", "output": str(self.config.output)},
        }

    def _load_or_create_state(self, fingerprint: str) -> None:
        if self.config.state.exists():
            self.state = load_state(self.config.state, expected_fingerprint=fingerprint)
            if len(self.state["chunks"]) != len(self.plan):
                raise ValueError("state chunk count does not match this plan")
        else:
            self.state = self._new_state(fingerprint)
            save_state(self.config.state, self.state)
        self._invalidate_missing_selected_artifacts()

    def _reset_chunks_from(self, index: int) -> None:
        for chunk in self.state["chunks"][index:]:
            chunk.update(
                status="pending",
                attempts=[],
                selected_attempt=None,
                selected_seed=None,
                gate_passed=False,
                metrics=None,
                video=None,
                last_frame=None,
            )
        self.state["assembly"]["status"] = "invalidated"

    def _invalidate_missing_selected_artifacts(self) -> None:
        changed = False
        for index, chunk in enumerate(self.state["chunks"]):
            if chunk.get("status") != "selected":
                continue
            video = chunk.get("video")
            if not video or not Path(video).is_file():
                # A replacement render changes the tail anchor, so every later
                # chunk is downstream of this cache miss and must be invalidated.
                self._reset_chunks_from(index)
                changed = True
                break
            anchor = chunk.get("last_frame")
            if not anchor or not Path(anchor).is_file():
                # The accepted video is still authoritative.  Re-extracting its
                # last frame is deterministic and must not trigger another render.
                repaired = self.config.work_dir / "anchors" / f"chunk_{index:03d}_last.png"
                self._extract_last_frame(Path(video), repaired)
                chunk["last_frame"] = str(repaired)
                changed = True
        if changed:
            save_state(self.config.state, self.state)

    def _texture_floor(self, chunk_index: int) -> float:
        previous = [
            float(chunk["metrics"]["pore"])
            for chunk in self.state["chunks"][:chunk_index]
            if chunk.get("status") == "selected"
            and isinstance(chunk.get("metrics"), dict)
            and _finite(chunk["metrics"].get("pore"))
        ]
        relative = 0.0
        if previous:
            relative = statistics.median(previous) * (1.0 - self.config.max_texture_drop)
        return max(self.config.min_texture, relative)

    def _render_attempt(self, **kwargs: object) -> Path:
        renderer_options = dict(kwargs)
        renderer_options.pop("chunk_index", None)
        renderer_options.pop("attempt", None)
        argv = build_renderer_argv(**renderer_options)  # type: ignore[arg-type]
        environment = dict(os.environ)
        environment["PYTHONPATH"] = str(REPO_ROOT)
        process = subprocess.run(
            argv,
            cwd=str(REPO_ROOT),
            env=environment,
            capture_output=True,
            text=True,
            check=False,
            timeout=6000,
        )
        if process.returncode:
            tail = ((process.stdout or "") + (process.stderr or ""))[-4000:]
            raise RuntimeError(f"H3 renderer failed ({process.returncode})\n{tail}")
        output = kwargs["output"]
        if not isinstance(output, Path) or not output.is_file():
            raise RuntimeError("renderer exited successfully but produced no chunk")
        return output

    def _measure_chunk(self, video: Path, attempt_dir: Path) -> dict[str, Any]:
        frames = extract_sample_frames(video, attempt_dir / "samples")
        return _texture_measure(frames)

    def _extract_last_frame(self, video: Path, output: Path) -> Path:
        info = probe_video(video)
        return extract_frame(video, output, info["frames"] - 1)

    def _select_attempt(self, chunk: dict[str, Any], selected: dict[str, Any]) -> None:
        chunk["status"] = "selected"
        chunk["selected_attempt"] = selected["attempt"]
        chunk["selected_seed"] = selected["seed"]
        chunk["gate_passed"] = bool(selected["accepted"])
        chunk["metrics"] = selected["metrics"]
        chunk["video"] = selected["video"]

    def _process_chunk(self, index: int, image_name: str) -> None:
        chunk = self.state["chunks"][index]
        if chunk.get("status") == "selected":
            return
        plan = self.plan[index]
        texture_floor = self._texture_floor(index)
        chunk["status"] = "rendering"
        save_state(self.config.state, self.state)

        while len(chunk["attempts"]) <= self.config.max_retries:
            attempt_number = len(chunk["attempts"])
            seed = seed_for(self.config.seed_base, index, attempt_number)
            attempt_dir = self.config.work_dir / f"chunk_{index:03d}" / f"attempt_{attempt_number:03d}"
            output = attempt_dir / f"chunk_{index}_{attempt_number}.mp4"
            record: dict[str, Any] = {
                "attempt": attempt_number,
                "seed": seed,
                "image_name": image_name,
                "video": str(output),
                "texture_floor": texture_floor,
                "accepted": False,
            }
            try:
                self._render_attempt(
                    chunk_index=index,
                    attempt=attempt_number,
                    seed=seed,
                    image_name=image_name,
                    output=output,
                    prompt_file=self.config.prompt_file,
                    audio=self.config.audio,
                    prefix=f"h3chain_{self.job_id}_c{index:03d}_a{attempt_number:03d}",
                    width=self.config.width,
                    height=self.config.height,
                    render_frames=plan.render_frames,
                    steps=self.config.steps,
                    audio_offset_frames=plan.output_start,
                    server=self.config.server,
                )
                metrics = self._measure_chunk(output, attempt_dir)
                gate = evaluate_gate(
                    metrics,
                    max_face_drift=self.config.max_face_drift,
                    texture_floor=texture_floor,
                )
                record.update(
                    status="accepted" if gate.accepted else "rejected",
                    accepted=gate.accepted,
                    metrics=metrics,
                    reasons=gate.reasons,
                    penalty=gate.penalty,
                )
            except Exception as exc:
                record.update(status="render_error", error=str(exc), reasons=[str(exc)])
            chunk["attempts"].append(record)
            save_state(self.config.state, self.state)
            if record["accepted"]:
                self._select_attempt(chunk, record)
                break
        else:
            existing = [
                attempt
                for attempt in chunk["attempts"]
                if attempt.get("video") and Path(attempt["video"]).is_file()
            ]
            selected = choose_best_attempt(
                existing,
                max_face_drift=self.config.max_face_drift,
                texture_floor=texture_floor,
            )
            self._select_attempt(chunk, selected)

        selected_video = Path(chunk["video"])
        last_frame = self.config.work_dir / "anchors" / f"chunk_{index:03d}_last.png"
        self._extract_last_frame(selected_video, last_frame)
        chunk["last_frame"] = str(last_frame)
        save_state(self.config.state, self.state)

    def _assemble(self, plan: Sequence[ChunkPlan]) -> None:
        """Trim boundary duplicates, concat video, and weld retained audio."""
        segments_dir = self.config.work_dir / "segments"
        segments_dir.mkdir(parents=True, exist_ok=True)
        segment_paths: list[Path] = []
        videos = [Path(chunk["video"]) for chunk in self.state["chunks"]]
        for index, (video, chunk_plan) in enumerate(zip(videos, plan)):
            segment = segments_dir / f"segment_{index:03d}.mp4"
            filters = []
            if index:
                filters.append("select=gte(n\\,1)")
            filters += [
                f"trim=end_frame={chunk_plan.kept_frames}",
                "setpts=PTS-STARTPTS",
                f"fps={FPS:g}",
            ]
            _run_command(
                [
                    "ffmpeg",
                    "-y",
                    "-v",
                    "error",
                    "-i",
                    str(video),
                    "-vf",
                    ",".join(filters),
                    "-an",
                    "-c:v",
                    "libx264",
                    "-crf",
                    "16",
                    "-pix_fmt",
                    "yuv420p",
                    "-video_track_timescale",
                    "90000",
                    str(segment),
                ]
            )
            segment_paths.append(segment)

        concat_file = segments_dir / "concat.txt"
        concat_file.write_text(
            "".join(f"file '{path.as_posix()}'\n" for path in segment_paths),
            encoding="utf-8",
            newline="\n",
        )
        video_only = self.config.work_dir / "video.mp4"
        _run_command(
            [
                "ffmpeg",
                "-y",
                "-v",
                "error",
                "-f",
                "concat",
                "-safe",
                "0",
                "-i",
                str(concat_file),
                "-c",
                "copy",
                str(video_only),
            ]
        )

        audio = self.config.work_dir / "audio.wav"
        audio_argv = ["ffmpeg", "-y", "-v", "error"]
        for video in videos:
            audio_argv += ["-i", str(video)]
        audio_argv += [
            "-filter_complex",
            build_audio_filter([chunk.kept_frames for chunk in plan]),
            "-map",
            "[aout]",
            "-c:a",
            "pcm_s16le",
            str(audio),
        ]
        _run_command(audio_argv)

        self.config.output.parent.mkdir(parents=True, exist_ok=True)
        _run_command(
            [
                "ffmpeg",
                "-y",
                "-v",
                "error",
                "-i",
                str(video_only),
                "-i",
                str(audio),
                "-map",
                "0:v:0",
                "-map",
                "1:a:0",
                "-c:v",
                "copy",
                "-c:a",
                "aac",
                "-b:a",
                "192k",
                "-shortest",
                "-movflags",
                "+faststart",
                str(self.config.output),
            ]
        )
        actual = probe_video(self.config.output)["frames"]
        if actual != self.config.total_frames:
            raise RuntimeError(
                f"assembled frame count {actual} does not match requested {self.config.total_frames}"
            )

    def _measure_seams(self, plan: Sequence[ChunkPlan]) -> list[dict[str, Any]]:
        seams: list[dict[str, Any]] = []
        seam_dir = self.config.work_dir / "seams"
        for index in range(1, len(plan)):
            before = Path(self.state["chunks"][index - 1]["last_frame"])
            after = extract_frame(
                Path(self.state["chunks"][index]["video"]),
                seam_dir / f"chunk_{index:03d}_first_retained.png",
                1,
            )
            before_metrics = _texture_measure([before])
            after_metrics = _texture_measure([after])
            pair = _texture_measure([before, after])
            before_pore = float(before_metrics["pore"])
            after_pore = float(after_metrics["pore"])
            ratio = after_pore / max(before_pore, 1e-12)
            seams.append(
                {
                    "boundary": f"{index - 1}->{index}",
                    "output_frame": plan[index].output_start,
                    "before_pore": before_pore,
                    "after_pore": after_pore,
                    "texture_ratio": ratio,
                    "texture_drop_pct": (1.0 - ratio) * 100.0,
                    "texture_ratchet": ratio < 1.0 - self.config.max_texture_drop,
                    "rgb_delta": pair["rgb_delta"],
                    "face_drift": pair["face_drift"],
                }
            )
        return seams

    def _report(self, seams: list[dict[str, Any]]) -> dict[str, Any]:
        selected = self.state["chunks"]
        drifts = [float(chunk["metrics"]["face_drift"]) for chunk in selected]
        pores = [float(chunk["metrics"]["pore"]) for chunk in selected]
        return {
            "schema_version": SCHEMA_VERSION,
            "job_id": self.job_id,
            "state": str(self.config.state),
            "output": str(self.config.output),
            "fps": FPS,
            "output_frames": self.config.total_frames,
            "crossfade_seconds": CROSSFADE_SECONDS,
            "chunks": selected,
            "seams": seams,
            "summary": {
                "quality_gate_passed": all(chunk["gate_passed"] for chunk in selected),
                "render_calls": sum(len(chunk["attempts"]) for chunk in selected),
                "retries": sum(max(0, len(chunk["attempts"]) - 1) for chunk in selected),
                "face_drift_mean": statistics.mean(drifts),
                "face_drift_max": max(drifts),
                "worst_face_drift_chunk": max(range(len(drifts)), key=drifts.__getitem__),
                "texture_pore_mean": statistics.mean(pores),
                "texture_pore_min": min(pores),
                "texture_ratchet_seams": sum(bool(seam["texture_ratchet"]) for seam in seams),
            },
        }

    def run(self) -> dict[str, Any]:
        self.config.work_dir.mkdir(parents=True, exist_ok=True)
        image_name = self._prepare_initial_image()
        fingerprint = self._fingerprint()
        self._load_or_create_state(fingerprint)

        for index in range(len(self.plan)):
            if index:
                previous_anchor = self.state["chunks"][index - 1]["last_frame"]
                if not previous_anchor:
                    raise RuntimeError("previous selected chunk has no last-frame anchor")
                image_name = self._stage_anchor(Path(previous_anchor), index - 1)
            self._process_chunk(index, image_name)

        self._assemble(self.plan)
        self.state["assembly"].update(status="complete", output=str(self.config.output))
        save_state(self.config.state, self.state)
        seams = self._measure_seams(self.plan)
        report = self._report(seams)
        self.state["assembly"]["seams"] = seams
        self.state["assembly"]["report"] = str(self.config.report_path)
        save_state(self.config.state, self.state)
        _write_json(self.config.report_path, report)
        return report


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--image", required=True, help="initial image path or ComfyUI input filename")
    parser.add_argument("--prompt-file", required=True, type=Path)
    parser.add_argument("--audio", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--chunk-frames", type=int, default=56, help="maximum H3 length, 5+17*k")
    parser.add_argument("--total-frames", type=int, default=166, help="exact frames after dedup/trim")
    parser.add_argument("--width", type=int, default=736)
    parser.add_argument("--height", type=int, default=1120)
    parser.add_argument("--steps", type=int, default=20)
    parser.add_argument("--seed-base", type=int, default=42)
    parser.add_argument("--max-retries", type=int, default=2)
    parser.add_argument("--max-face-drift", type=float, default=8.0)
    parser.add_argument("--min-texture", type=float, default=0.0, help="optional absolute pore-energy floor")
    parser.add_argument(
        "--max-texture-drop",
        type=float,
        default=0.25,
        help="relative pore floor versus median of previous selected chunks",
    )
    parser.add_argument("--state", type=Path, default=None)
    parser.add_argument("--server", default="")
    parser.add_argument("--plan", action="store_true", help="print the chunk table without any I/O or rendering")
    return parser


def _config_from_args(args: argparse.Namespace) -> ChainConfig:
    output = Path(args.output)
    state = args.state or output.with_suffix(output.suffix + ".state.json")
    config = ChainConfig(
        image=args.image,
        prompt_file=args.prompt_file,
        audio=args.audio,
        output=output,
        chunk_frames=args.chunk_frames,
        total_frames=args.total_frames,
        width=args.width,
        height=args.height,
        steps=args.steps,
        seed_base=args.seed_base,
        max_retries=args.max_retries,
        max_face_drift=args.max_face_drift,
        min_texture=args.min_texture,
        max_texture_drop=args.max_texture_drop,
        state=state,
        server=args.server,
    )
    if config.max_retries < 0:
        raise ValueError("max retries must be non-negative")
    if config.max_face_drift <= 0:
        raise ValueError("max face drift must be positive")
    if not 0.0 <= config.max_texture_drop < 1.0:
        raise ValueError("max texture drop must be in [0, 1)")
    if config.min_texture < 0 or config.steps < 1 or config.width < 1 or config.height < 1:
        raise ValueError("texture, steps, and dimensions must be positive")
    return config


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(list(argv) if argv is not None else None)
    try:
        config = _config_from_args(args)
        plan = build_chunk_plan(config.total_frames, config.chunk_frames, config.seed_base)
        if args.plan:
            print(format_plan(plan, config.total_frames))
            return 0
        report = ChainPipeline(config).run()
    except (OSError, RuntimeError, ValueError, KeyError) as exc:
        print(f"h3 chain failed: {exc}", file=sys.stderr)
        return 2
    print(json.dumps(report["summary"], ensure_ascii=False, indent=2))
    print(f"report: {config.report_path}")
    return 0 if report["summary"]["quality_gate_passed"] else 3


if __name__ == "__main__":
    raise SystemExit(main())
