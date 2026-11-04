# -*- coding: utf-8 -*-
"""Putting the chunks back together.

Three things have to happen at the seam, and all three were wrong at some
point:

1. **The duplicated boundary frame is dropped.** Every chunk after the first
   starts on the last frame of its predecessor, so that frame exists twice.
   Keeping both copies shows a frozen frame at every join.

2. **The audio is welded, not cut.** The source track is cut at the chunk
   boundaries and the pieces are joined with a 40 ms equal-power crossfade.
   A hard cut clicks; a crossfade does not.

   The filter graph is reused from ``scripts/h3_chain_video.py``
   (``build_audio_weld_filters``) instead of being written again: the curve
   name (``qsin``) and the label syntax (``[s0][s1]acrossfade=...``) are
   both easy to get wrong, and one working implementation beats two.

   Two details this module adds on top of that helper:

   * the boundaries are passed in SECONDS. The older runner feeds it frame
     counts, which makes ffmpeg cut the track at the wrong places (a 56
     frame boundary is 2.3 s, not 56 s);
   * every boundary is shifted forward by the number of crossfades that came
     before it. Each ``acrossfade`` consumes 40 ms, so without the shift
     the audio drifts ahead of the picture by 40 ms per seam and the drift
     accumulates over the clip.

3. **The seam is measured.** The texture and the face are compared across
   the boundary, because that is where the "texture ratchet" was visible:
   the joins looked rougher and more detailed than the clips around them.
"""
from __future__ import annotations

import subprocess
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from . import paths

__all__ = [
    "CROSSFADE_SECONDS",
    "AssemblyResult",
    "audio_boundaries",
    "build_audio_weld_filters",
    "concat_chunks",
    "seam_metrics",
    "weld_audio",
]

# 40 ms: long enough to hide a cut, short enough not to be heard as a
# change of room.
CROSSFADE_SECONDS = 0.04


def build_audio_weld_filters(boundaries: list[float]) -> list[str]:
    """The ffmpeg filter graph that welds an audio track at the boundaries.

    Reused from ``scripts/h3_chain_video.py``. ``boundaries`` are cut
    positions in SECONDS.
    """
    from scripts.h3_chain_video import build_audio_weld_filters as _build

    return _build(boundaries)


def audio_boundaries(
    seam_seconds: list[float],
    crossfade: float = CROSSFADE_SECONDS,
) -> list[float]:
    """Cut positions for the weld, in SECONDS.

    The positions are the seams of the finished clip. Each ``acrossfade``
    consumes ``crossfade`` seconds of track - the two halves of the seam are
    blended into one - so the welded track is that much shorter per seam.
    Cutting a little LATER for every seam that came before puts the missing
    time back: the material that belongs at a seam is then the material that
    is heard at it.

    Measured with a click track (10 s, clicks every second, cuts at 3 s and
    6 s): with the raw positions the click that belongs at 4 s is heard at
    3.96 s and the one that belongs at 7 s at 6.92 s - the track creeps
    ahead of the picture by 40 ms per seam. With the compensation the clicks
    land back on 4 s and 7 s.
    """
    return [
        position + index * crossfade
        for index, position in enumerate(seam_seconds)
    ]


def weld_audio(
    audio: Path,
    boundaries: list[float],
    out: Path,
    duration: float = 0.0,
) -> Path:
    """Write the welded audio track to ``out``.

    ``boundaries`` are cut positions in SECONDS. When ``duration`` is given,
    the track is padded or trimmed to exactly that length, so the audio
    stream is never shorter than the picture.
    """
    out = Path(out)
    out.parent.mkdir(parents=True, exist_ok=True)
    if not boundaries:
        cmd = [
            paths.ffmpeg_exe(), "-y", "-v", "error", "-i", str(audio),
            "-c:a", "copy", str(out),
        ]
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=1800)
        if proc.returncode != 0 or not out.exists():
            raise RuntimeError(f"could not copy the audio: {proc.stderr[:400]}")
        return out
    filters = build_audio_weld_filters(boundaries)
    if duration > 0:
        # The helper ends with a 0.5 s pad. This makes the track exactly as
        # long as the picture: apad fills it up if the weld came out short,
        # atrim cuts it if it came out long. Without the atrim the track
        # stays longer than the picture and ffmpeg's -shortest would cut
        # the VIDEO instead.
        filters = filters + [f"[aout]apad,atrim=end={duration:.6f}[afinal]"]
        label = "[afinal]"
    else:
        label = "[aout]"
    cmd = [
        paths.ffmpeg_exe(), "-y", "-v", "error", "-i", str(audio),
        "-filter_complex", ";".join(filters), "-map", label,
        "-c:a", "aac", "-b:a", "192k", str(out),
    ]
    proc = subprocess.run(cmd, capture_output=True, text=True, timeout=1800)
    if proc.returncode != 0 or not out.exists():
        raise RuntimeError(f"could not weld the audio: {proc.stderr[:400]}")
    return out


def concat_chunks(
    chunk_paths: list[Path],
    out: Path,
    total_frames: int = 0,
) -> Path:
    """Concatenate the chunks, dropping the duplicated boundary frame.

    Every chunk after the first starts with the frame its predecessor ended
    on, so that frame is trimmed away before the join. The result is trimmed
    to ``total_frames`` when given, because the last chunk is rounded up to
    a length the H3 sampler accepts and would otherwise overshoot.
    """
    if not chunk_paths:
        raise ValueError("no chunks to concatenate")
    out = Path(out)
    out.parent.mkdir(parents=True, exist_ok=True)

    cmd = [paths.ffmpeg_exe(), "-y", "-v", "error"]
    for path in chunk_paths:
        cmd += ["-i", str(path)]
    parts: list[str] = []
    for index in range(len(chunk_paths)):
        if index == 0:
            parts.append(f"[0:v]trim=start_frame=0,setpts=PTS-STARTPTS[v{index}]")
        else:
            # The first frame of this chunk duplicates the last frame of the
            # previous one.
            parts.append(
                f"[{index}:v]trim=start_frame=1,setpts=PTS-STARTPTS[v{index}]"
            )
    graph = ";".join(parts) + (
        f";{''.join(f'[v{i}]' for i in range(len(chunk_paths)))}"
        f"concat=n={len(chunk_paths)}:v=1:a=0[vout]"
    )
    cmd += [
        "-filter_complex", graph, "-map", "[vout]",
        "-c:v", "libx264", "-crf", "16", "-pix_fmt", "yuv420p",
    ]
    if total_frames > 0:
        cmd += ["-frames:v", str(total_frames)]
    cmd += [str(out)]
    proc = subprocess.run(cmd, capture_output=True, text=True, timeout=7200)
    if proc.returncode != 0 or not out.exists():
        raise RuntimeError(f"could not concatenate the chunks: {proc.stderr[:600]}")
    return out


@dataclass
class AssemblyResult:
    """The assembled clip and what was measured at its seams."""

    video: Path
    audio: Path
    seams: list[dict[str, Any]] = field(default_factory=list)

    def to_json(self) -> dict[str, Any]:
        return {
            "video": str(self.video),
            "audio": str(self.audio),
            "seams": list(self.seams),
        }


def seam_metrics(
    chunk_paths: list[Path],
    face_norm: tuple[float, float, float, float] | None = None,
    frames_per_side: int = 8,
) -> list[dict[str, Any]]:
    """Measure every join between two chunks.

    For each boundary the last frames of the left chunk and the first frames
    of the right chunk are compared inside the face ROI:

    * ``face_delta`` - how much the face moved across the seam;
    * ``texture_ratio`` - the pore energy after the seam relative to before
      it. A ratio far from 1.0 is the texture ratchet.
    """
    if len(chunk_paths) < 2:
        return []
    if face_norm is None:
        from scripts.probe_face_drift import FACE_NORM

        face_norm = FACE_NORM

    import numpy as np
    from PIL import Image

    def frames_of(video: Path, side: str) -> list[np.ndarray]:
        """The frames of one side of a seam, as pixel arrays.

        The frames are read inside the temporary directory: handing out paths
        that live in a directory that is deleted on return would fail later,
        far away from the cause.
        """
        with tempfile.TemporaryDirectory(prefix="h3chain_seam_") as tmp:
            out_dir = Path(tmp)
            if side == "head":
                cmd = [
                    paths.ffmpeg_exe(), "-y", "-v", "error", "-i", str(video),
                    "-frames:v", str(frames_per_side + 1),
                    "-fps_mode", "passthrough", str(out_dir / "f_%05d.png"),
                ]
            else:
                cmd = [
                    paths.ffmpeg_exe(), "-y", "-v", "error", "-sseof", "-2",
                    "-i", str(video), "-frames:v", str(frames_per_side),
                    "-fps_mode", "passthrough", str(out_dir / "f_%05d.png"),
                ]
            subprocess.run(cmd, capture_output=True, text=True, timeout=1800)
            files = sorted(out_dir.glob("f_*.png"))
            if side == "head" and files:
                # The first frame of the right chunk duplicates the last frame
                # of the left one; it is trimmed on concat, so it is not part
                # of the seam.
                files = files[1:]
            return [
                np.asarray(Image.open(path).convert("RGB"), dtype=np.float32)
                for path in files
            ]

    def patch_of(arr: np.ndarray) -> np.ndarray:
        height, width = arr.shape[:2]
        x1, y1 = int(face_norm[0] * width), int(face_norm[1] * height)
        x2, y2 = int(face_norm[2] * width), int(face_norm[3] * height)
        return arr[y1:y2, x1:x2]

    def pore_energy(frames: list[np.ndarray]) -> float:
        """Pore-band energy of the frames, via the repository's own metric."""
        try:
            from scripts.probe_pore_spectrum import band_energy, roi_slice
        except ImportError:
            return 0.0
        if not frames:
            return 0.0
        total = 0.0
        for arr in frames:
            gray = arr.mean(axis=2)
            y0, y1, x0, x1 = roi_slice(arr.shape[:2], face_norm)
            crop = gray[y0:y1, x0:x1]
            if crop.size == 0:
                continue
            total += float(band_energy(crop, 3.0, 12.0))
        return total / len(frames)

    seams: list[dict[str, Any]] = []
    for index in range(len(chunk_paths) - 1):
        left = frames_of(Path(chunk_paths[index]), "tail")
        right = frames_of(Path(chunk_paths[index + 1]), "head")
        if not left or not right:
            continue
        face_delta = float(
            np.abs(patch_of(left[-1]) - patch_of(right[0])).mean()
        )
        pore_before = pore_energy(left)
        pore_after = pore_energy(right)
        ratio = (pore_after / pore_before) if pore_before > 0 else 0.0
        seams.append({
            "index": index,
            "left": str(chunk_paths[index]),
            "right": str(chunk_paths[index + 1]),
            "face_delta": round(face_delta, 4),
            "pore_before": float(f"{pore_before:.6g}"),
            "pore_after": float(f"{pore_after:.6g}"),
            "texture_ratio": round(ratio, 4),
        })
    return seams
