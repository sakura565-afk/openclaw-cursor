# -*- coding: utf-8 -*-
"""The H3 chain pipeline: a long clip that keeps its face.

    python scripts/h3_chain_pipeline/pipeline.py --plan \
        --image still.png --prompt-file prompt.txt --audio track.m4a \
        --output out.mp4 --total-frames 2100

    python scripts/h3_chain_pipeline/pipeline.py \
        --image still.png --prompt-file prompt.txt --audio track.m4a \
        --output out.mp4 --total-frames 2100 --state out.state.json

The problem
-----------
A long H3 clip drifts: the face is fine at the start and walks away towards
the tail. A guide frame placed in the middle of the clip does NOT fix it -
a guide holds about two frames, because the VAE smooths around it, so the
tail keeps floating. The only thing that holds a long clip is to re-anchor
every chunk on a frame the model produced itself.

The pipeline
------------
1. **Plan.** The clip is split into chunks of at most ``--chunk-frames``
   frames (175 by default: 7.3 s at 24 fps, the sweet spot on a 16 GB
   card). ``--plan`` prints the table and touches nothing.

2. **Render.** Every chunk is one call to ``scripts/h3_i2v_run.py``. The
   first chunk starts on ``--image``; every later chunk starts on the LAST
   frame of its predecessor. Each chunk has its own seed; the prompt is
   repeated verbatim.

3. **Gate.** Every chunk is measured (face drift, texture) and re-rendered
   with a new seed if it fails. Only the failing chunk is re-rendered.

4. **Assemble.** The duplicated boundary frame is trimmed, the chunks are
   concatenated, the audio is welded with a 40 ms equal-power crossfade, and
   the seams are measured.

5. **Resume.** A JSON state file records which chunks were accepted. A
   second run does not re-render them - renders cost minutes of GPU time.

Environment
-----------
``COMFYUI_PROD_DIR``   ComfyUI portable root (input dir is derived from it)
``COMFYUI_INPUT_DIR``  explicit override for the input directory
``COMFYUI_PROD_URL``   the ComfyUI server URL (``--server`` sets it)
``HERMES_SCRATCH_DIR`` where the intermediate chunks are written
``HERMES_PYTHON``      interpreter used to launch the renderer
``FFMPEG_BIN``         ffmpeg binary
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

if __package__ in (None, ""):  # pragma: no cover - direct script execution
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from scripts.h3_chain_pipeline import assemble, gates, paths, plan, render, state
from scripts.h3_chain_pipeline.gates import ChunkMetrics
from scripts.h3_chain_pipeline.plan import Plan
from scripts.h3_chain_pipeline.render import RenderRequest
from scripts.h3_chain_pipeline.state import ChunkRecord, State

__all__ = ["main", "run"]

# The runner draws the face ROI from this box, measured on an H3 frame.
DEFAULT_WIDTH = 1056
DEFAULT_HEIGHT = 704
DEFAULT_STEPS = 14
DEFAULT_MAX_RETRIES = 2
# A frame "holds" the face while its drift stays under 8/255; the limit for
# the tail of a chunk is a little above that.
DEFAULT_MAX_FACE_DRIFT = 8.0
DEFAULT_MIN_TEXTURE_RATIO = 0.75
FPS = 24


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="h3_chain_pipeline",
        description="Long H3 clips that keep their face (chunked, gated, resumable).",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--image", required=True,
                        help="first still (a path, or a name in the ComfyUI input dir)")
    parser.add_argument("--prompt-file", required=True,
                        help="the character description; repeated verbatim in every chunk")
    parser.add_argument("--audio", required=True,
                        help="the source track; it is welded onto the assembled clip")
    parser.add_argument("--output", required=True, help="the finished mp4")
    parser.add_argument("--chunk-frames", type=int, default=plan.DEFAULT_CHUNK_FRAMES,
                        help="frames per chunk (default 175 = 7.3 s at 24 fps)")
    parser.add_argument("--total-frames", type=int, default=0,
                        help="frames of the finished clip (0 = as long as the audio)")
    parser.add_argument("--width", type=int, default=DEFAULT_WIDTH)
    parser.add_argument("--height", type=int, default=DEFAULT_HEIGHT)
    parser.add_argument("--steps", type=int, default=DEFAULT_STEPS)
    parser.add_argument("--seed-base", type=int, default=42,
                        help="seed of the first chunk; every chunk gets its own")
    parser.add_argument("--max-retries", type=int, default=DEFAULT_MAX_RETRIES,
                        help="re-renders allowed for a chunk that fails the gate")
    parser.add_argument("--max-face-drift", type=float, default=DEFAULT_MAX_FACE_DRIFT,
                        help="the tail drift above which a chunk is rejected")
    parser.add_argument("--min-texture-ratio", type=float, default=DEFAULT_MIN_TEXTURE_RATIO,
                        help="texture a chunk must keep relative to the first chunk")
    parser.add_argument("--plan", action="store_true",
                        help="print the chunk table and exit; nothing is rendered")
    parser.add_argument("--state", default="",
                        help="the state file (default: <output>.state.json)")
    parser.add_argument("--server", default="",
                        help="the ComfyUI server URL")
    parser.add_argument("--work", default="",
                        help="where the chunks are written (default: the scratch dir)")
    parser.add_argument("--report", default="",
                        help="where the JSON report is written (default: <output>.report.json)")
    parser.add_argument("--loras-json", default="[]", help="passed to the renderer")
    parser.add_argument("--negative", default="", help="passed to the renderer")
    parser.add_argument("--wait", type=int, default=7200,
                        help="seconds the renderer may wait for a chunk")
    parser.add_argument("--keep-going", action="store_true",
                        help="assemble the clip even if a chunk never passes the gate")
    return parser


def _state_path(args: argparse.Namespace) -> Path:
    if args.state:
        return Path(args.state)
    output = Path(args.output)
    return output.with_name(output.stem + ".state.json")


def _report_path(args: argparse.Namespace) -> Path:
    if args.report:
        return Path(args.report)
    output = Path(args.output)
    return output.with_name(output.stem + ".report.json")


def _work_dir(args: argparse.Namespace) -> Path:
    if args.work:
        return Path(args.work)
    output = Path(args.output)
    return paths.scratch_dir() / "h3_chain_pipeline" / output.stem


def _audio_seconds(audio: Path) -> float:
    """Length of the source track in seconds."""
    cmd = [
        paths.ffprobe_exe(), "-v", "error", "-show_entries", "format=duration",
        "-of", "csv=p=0", str(audio),
    ]
    proc = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
    try:
        return float((proc.stdout or "0").strip().split(",")[0])
    except ValueError:
        return 0.0


def _source_image(args: argparse.Namespace) -> Path:
    """The still the first chunk starts on.

    The runner's ``LoadImage`` node only accepts a FILE NAME from the
    ComfyUI input directory, so the still is copied there and only the name
    is passed on.
    """
    candidate = Path(args.image)
    if candidate.exists():
        return candidate
    in_input = paths.comfy_input_dir() / candidate.name
    if in_input.exists():
        return in_input
    raise SystemExit(
        f"the still is not on disk and not in the ComfyUI input dir: {args.image}"
    )


def _plan_table(the_plan: Plan, digest: str) -> str:
    lines = [
        f"chunk  seed      frames  out frames        seconds  note",
        f"-----  --------  ------  ---------------  -------  ----------------",
    ]
    for chunk in the_plan.chunks:
        note = ""
        if chunk.render_frames != chunk.out_frames:
            note = (
                f"renders {chunk.render_frames}, contributes {chunk.out_frames}"
            )
        lines.append(
            f"{chunk.index:>5}  {chunk.seed:>8}  {chunk.render_frames:>6}  "
            f"{chunk.out_first:>6}..{chunk.out_last:<6}  "
            f"{chunk.duration_seconds:>7.2f}  {note}"
        )
    lines.append("")
    lines.append(
        f"total: {len(the_plan.chunks)} chunks, {the_plan.assembled_frames} frames "
        f"rendered, {the_plan.total_frames} frames in the finished clip "
        f"({the_plan.duration_seconds:.2f} s)"
    )
    lines.append(f"prompt hash: {digest}")
    for note in the_plan.notes:
        lines.append(f"note: {note}")
    return "\n".join(lines)


def _chunk_file(work: Path, index: int, attempt: int) -> Path:
    return work / f"chunk_{index:03d}_a{attempt}.mp4"


def _chunk_frame_file(work: Path, index: int, attempt: int) -> Path:
    return work / f"chunk_{index:03d}_a{attempt}_last.png"


def _render_one(
    args: argparse.Namespace,
    chunk: plan.ChunkPlan,
    attempt: int,
    seed: int,
    anchor: Path,
    work: Path,
    input_dir: Path,
    silent: Path,
    prompt_file: Path,
) -> Path:
    """Render one attempt of one chunk and return the produced file."""
    image_name = render.copy_into_input(
        anchor, input_dir, f"h3chain_c{chunk.index:03d}_a{attempt}.png"
    )
    output = _chunk_file(work, chunk.index, attempt)
    request = RenderRequest(
        image_name=image_name,
        prompt_file=prompt_file,
        audio=silent,
        output=output,
        length=chunk.render_frames,
        seed=seed,
        width=args.width,
        height=args.height,
        steps=args.steps,
        prefix=f"h3chain_c{chunk.index:03d}_a{attempt}",
        server=args.server,
        loras_json=args.loras_json,
        negative=args.negative,
        wait=args.wait,
    )
    return render.render_chunk(request)


def _measure_and_gate(
    video: Path,
    baseline_pore: float,
    args: argparse.Namespace,
) -> tuple[gates.GateDecision, ChunkMetrics]:
    """Measure a rendered chunk and decide whether it may enter the clip."""
    metrics = gates.measure_chunk(video, baseline_pore=baseline_pore)
    decision = gates.evaluate_chunk(
        metrics, args.max_face_drift, args.min_texture_ratio
    )
    return decision, metrics


def run(args: argparse.Namespace) -> int:
    """Do the whole job. Returns the process exit code."""
    work = _work_dir(args)
    prompt_file = Path(args.prompt_file)
    digest = plan.prompt_hash(prompt_file)

    audio = Path(args.audio)
    total_frames = args.total_frames
    if total_frames <= 0:
        seconds = _audio_seconds(audio)
        total_frames = int(round(seconds * FPS))
        if total_frames <= 0:
            raise SystemExit(
                "could not work out the length of the clip: pass --total-frames"
            )
    the_plan = plan.plan_chunks(
        total_frames=total_frames,
        chunk_frames=args.chunk_frames,
        seed_base=args.seed_base,
        fps=FPS,
    )

    if args.plan:
        print(_plan_table(the_plan, digest))
        return 0

    work.mkdir(parents=True, exist_ok=True)
    state_file = _state_path(args)
    pipeline_state = State.load(state_file)
    report: dict[str, Any] = {
        "output": str(Path(args.output)),
        "state": str(state_file),
        "prompt_hash": digest,
        "started": time.time(),
        "plan": {
            "total_frames": the_plan.total_frames,
            "assembled_frames": the_plan.assembled_frames,
            "chunk_frames_requested": the_plan.chunk_frames_requested,
            "chunk_frames_effective": the_plan.chunk_frames_effective,
            "fps": the_plan.fps,
            "chunks": [
                {
                    "index": chunk.index,
                    "seed": chunk.seed,
                    "render_frames": chunk.render_frames,
                    "out_first": chunk.out_first,
                    "out_last": chunk.out_last,
                }
                for chunk in the_plan.chunks
            ],
        },
        "gate": {
            "max_face_drift": args.max_face_drift,
            "min_texture_ratio": args.min_texture_ratio,
            "max_retries": args.max_retries,
        },
        "chunks": [],
    }

    input_dir = paths.comfy_input_dir()
    source = _source_image(args)
    silent = render.make_silent_wav(
        work / "silent.wav", max(1.0, the_plan.duration_seconds + 1.0)
    )

    # The texture baseline is the first chunk that passes the gate.
    baseline_pore = 0.0
    for record in pipeline_state.records():
        if record.accepted and record.metrics.get("pore"):
            baseline_pore = float(record.metrics["pore"])
            break

    chunk_reports: list[dict[str, Any]] = []
    accepted_videos: list[Path] = []
    anchor = source

    for chunk in the_plan.chunks:
        # The chunk may only be reused when it was rendered with a seed this
        # run would have chosen: otherwise a changed --seed-base would be
        # silently ignored and the old takes would go into the clip.
        allowed_seeds = {
            the_plan.seed_for(chunk.index, attempt)
            for attempt in range(args.max_retries + 1)
        }
        cached = pipeline_state.accepted_for(
            chunk.index, digest, anchor=str(anchor), seeds=allowed_seeds
        )
        if cached is not None:
            # Already rendered and accepted on an earlier run: keep it.
            print(
                f"chunk {chunk.index}: reusing the accepted render "
                f"(seed {cached.seed})",
                flush=True,
            )
            accepted_videos.append(Path(cached.video))
            anchor = Path(cached.last_frame) if cached.last_frame else anchor
            if baseline_pore <= 0 and cached.metrics.get("pore"):
                baseline_pore = float(cached.metrics["pore"])
            chunk_reports.append({
                "index": chunk.index,
                "seed": cached.seed,
                "attempts": cached.attempts,
                "accepted": True,
                "reused": True,
                "metrics": cached.metrics,
                "reason": cached.reason,
            })
            continue

        best: tuple[ChunkMetrics, Path, int, int] | None = None
        attempts_report: list[dict[str, Any]] = []
        for attempt in range(args.max_retries + 1):
            seed = the_plan.seed_for(chunk.index, attempt)
            print(
                f"chunk {chunk.index}: attempt {attempt + 1}/{args.max_retries + 1} "
                f"(seed {seed})",
                flush=True,
            )
            try:
                video = _render_one(
                    args, chunk, attempt, seed, anchor, work,
                    input_dir, silent, prompt_file,
                )
            except render.RenderError as exc:
                attempts_report.append({
                    "attempt": attempt,
                    "seed": seed,
                    "accepted": False,
                    "reason": f"render failed: {exc}",
                })
                print(f"  render failed: {exc}", flush=True)
                continue

            decision, metrics = _measure_and_gate(
                video, baseline_pore, args
            )
            attempts_report.append({
                "attempt": attempt,
                "seed": seed,
                "accepted": decision.accepted,
                "reason": decision.reason,
                "metrics": metrics.to_json(),
            })
            print(f"  {decision.reason}", flush=True)
            if decision.accepted:
                best = (metrics, video, attempt, seed)
                break
            if best is None or metrics.score() < best[0].score():
                best = (metrics, video, attempt, seed)

        if best is None:
            chunk_reports.append({
                "index": chunk.index,
                "seed": the_plan.seed_for(chunk.index, 0),
                "attempts": attempts_report,
                "accepted": False,
                "reused": False,
                "reason": "every attempt failed to render",
                "metrics": {},
            })
            if not args.keep_going:
                raise SystemExit(
                    f"chunk {chunk.index} never rendered; the clip cannot be assembled"
                )
            continue

        metrics, video, attempt, seed = best
        accepted = any(item["accepted"] for item in attempts_report)
        last_frame = render.extract_last_frame(
            video, _chunk_frame_file(work, chunk.index, attempt)
        )
        record = ChunkRecord(
            index=chunk.index,
            seed=seed,
            prompt_hash=digest,
            accepted=accepted,
            attempts=len(attempts_report),
            video=str(video),
            last_frame=str(last_frame),
            anchor=str(anchor),
            metrics=metrics.to_json(),
            reason="; ".join(
                item["reason"] for item in attempts_report if item["accepted"]
            ) or "best attempt, still outside the limits",
        )
        pipeline_state.put(record)
        accepted_videos.append(video)
        anchor = last_frame
        if baseline_pore <= 0 and metrics.pore:
            baseline_pore = metrics.pore
        chunk_reports.append({
            "index": chunk.index,
            "seed": seed,
            "attempts": attempts_report,
            "accepted": accepted,
            "reused": False,
            "metrics": metrics.to_json(),
            "reason": record.reason,
        })

    report["chunks"] = chunk_reports
    report["baseline_pore"] = baseline_pore

    if not accepted_videos:
        raise SystemExit("no chunk was rendered; nothing to assemble")

    # Assemble: trim the duplicated boundary frames, concatenate, weld the
    # audio.
    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    video_only = work / "video.mp4"
    assemble.concat_chunks(accepted_videos, video_only, total_frames=the_plan.total_frames)

    seam_seconds = the_plan.render_seams_seconds()
    weld_boundaries = assemble.audio_boundaries(seam_seconds)
    welded = assemble.weld_audio(
        audio, weld_boundaries, work / "audio.m4a",
        duration=the_plan.duration_seconds,
    )

    mux_cmd = [
        paths.ffmpeg_exe(), "-y", "-v", "error",
        "-i", str(video_only), "-i", str(welded),
        "-map", "0:v:0", "-map", "1:a:0",
        "-c:v", "copy", "-c:a", "aac", "-b:a", "192k",
        "-shortest", str(output),
    ]
    proc = subprocess.run(mux_cmd, capture_output=True, text=True, timeout=3600)
    if proc.returncode != 0 or not output.exists():
        raise SystemExit(f"could not mux the final clip: {proc.stderr[:600]}")

    # Measure the assembled clip and its seams.
    report["seams"] = assemble.seam_metrics(accepted_videos)
    try:
        drift = gates.measure_clip_drift(output)
        report["clip_drift"] = {
            "frames": drift["frames"],
            "face_drift_mean": drift["face_drift_mean"],
            "face_drift_max": drift["face_drift_max"],
            "face_drift_last": drift["face_drift_last"],
            "frames_holding_face": drift["frames_holding_face"],
        }
    except Exception as exc:  # the measurement must not lose the clip
        report["clip_drift"] = {"error": str(exc)}

    report["output_bytes"] = output.stat().st_size
    report["finished"] = time.time()
    report_path = _report_path(args)
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(
        json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    pipeline_state.set_assembly({
        "output": str(output),
        "seams": report["seams"],
        "clip_drift": report.get("clip_drift", {}),
        "report": str(report_path),
    })
    print(f"saved: {output} ({output.stat().st_size / 1e6:.2f} MB)", flush=True)
    print(f"report: {report_path}", flush=True)
    return 0


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    return run(args)


if __name__ == "__main__":
    raise SystemExit(main())
