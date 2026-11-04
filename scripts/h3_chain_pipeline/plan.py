# -*- coding: utf-8 -*-
"""Chunk arithmetic for the H3 chain pipeline.

Pure functions only: no filesystem, no ComfyUI, no ffmpeg. Everything here is
directly testable, which is what makes ``--plan`` trustworthy - the plan the
user approves is literally the plan the renderer executes.

The frame accounting
--------------------
Every chunk after the first one starts on the LAST frame of the previous
chunk. That frame is rendered twice, so it is trimmed once on concat. The
consequences, spelled out because they drive the whole arithmetic:

* chunk 0 contributes ``render`` frames to the assembled clip;
* every later chunk contributes ``render - 1`` frames (its first frame is the
  duplicate of the previous chunk's last frame);
* ``N`` chunks of ``L`` frames therefore assemble into ``L + (N - 1) * (L - 1)``
  frames, not ``N * L``.

The H3 length constraint
------------------------
H3 accepts clip lengths of ``17k + 5`` frames (56 is a safe short block, and
175 - the default - is 17 * 10 + 5). A length outside that set is rejected by
the sampler, so a requested chunk size is snapped to the nearest valid one
below it and the plan says so out loud.

Why chunking at all
--------------------
A guide frame placed in the middle of a clip does NOT hold it: the guide
holds roughly two frames because the VAE smooths around it, and the tail of
the clip keeps floating. The only thing that holds a long clip is to re-anchor
every chunk on a frame the model produced itself.
"""
from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from pathlib import Path

__all__ = [
    "DEFAULT_CHUNK_FRAMES",
    "DEFAULT_FPS",
    "H3_LENGTH_OFFSET",
    "H3_LENGTH_STEP",
    "ChunkPlan",
    "Plan",
    "h3_valid_length",
    "next_valid_length",
    "plan_chunks",
    "prompt_hash",
    "retry_seed",
]

# 175 frames = 7.29 s at 24 fps. This is the sweet spot on a 16 GB card.
DEFAULT_CHUNK_FRAMES = 175
DEFAULT_FPS = 24
# H3 accepts 17k + 5 frames.
H3_LENGTH_STEP = 17
H3_LENGTH_OFFSET = 5
# The smallest length worth rendering (k = 1).
MIN_H3_LENGTH = H3_LENGTH_STEP + H3_LENGTH_OFFSET
# Retry seeds are this far apart so a retried chunk never reuses a seed.
RETRY_SEED_STRIDE = 1000


def h3_valid_length(frames: int) -> bool:
    """True when ``frames`` is a length the H3 sampler accepts (17k + 5)."""
    if frames < MIN_H3_LENGTH:
        return False
    return (frames - H3_LENGTH_OFFSET) % H3_LENGTH_STEP == 0


def next_valid_length(frames: int) -> int:
    """Smallest valid H3 length that is >= ``frames``."""
    if frames <= MIN_H3_LENGTH:
        return MIN_H3_LENGTH
    steps = -(-(frames - H3_LENGTH_OFFSET) // H3_LENGTH_STEP)
    return steps * H3_LENGTH_STEP + H3_LENGTH_OFFSET


def _largest_valid_length_at_most(frames: int) -> int:
    """Largest valid H3 length that is <= ``frames`` (never below the minimum)."""
    if frames <= MIN_H3_LENGTH:
        return MIN_H3_LENGTH
    steps = (frames - H3_LENGTH_OFFSET) // H3_LENGTH_STEP
    if steps < 1:
        return MIN_H3_LENGTH
    return steps * H3_LENGTH_STEP + H3_LENGTH_OFFSET


def retry_seed(seed_base: int, index: int, attempt: int) -> int:
    """Deterministic seed for chunk ``index`` on attempt ``attempt``.

    Attempt 0 is the seed the plan shows. A retry moves the seed by
    ``RETRY_SEED_STRIDE`` so the new take cannot be the rejected one, and the
    value is a pure function of the inputs so a resumed run reproduces it.
    """
    return seed_base + index + attempt * RETRY_SEED_STRIDE


@dataclass(frozen=True)
class ChunkPlan:
    """One chunk of the chain."""

    index: int
    seed: int
    render_frames: int
    """Frames the renderer produces for this chunk (the H3 ``--length``)."""
    out_first: int
    """First frame of this chunk in the ASSEMBLED clip."""
    out_last: int
    """Last frame of this chunk in the ASSEMBLED clip (inclusive)."""
    start_seconds: float
    """Position of the chunk in the assembled clip, in seconds."""

    @property
    def out_frames(self) -> int:
        """Frames this chunk contributes to the assembled clip."""
        return self.out_last - self.out_first + 1

    @property
    def duration_seconds(self) -> float:
        return self.out_frames / 24.0


@dataclass(frozen=True)
class Plan:
    """The whole chain: the chunk table plus the totals it produces."""

    chunks: tuple[ChunkPlan, ...]
    total_frames: int
    """Frames the assembled clip is trimmed to."""
    assembled_frames: int
    """Frames the chunks produce before the final trim."""
    chunk_frames_requested: int
    chunk_frames_effective: int
    fps: int = DEFAULT_FPS
    seed_base: int = 0
    notes: tuple[str, ...] = field(default_factory=tuple)

    @property
    def duration_seconds(self) -> float:
        return self.total_frames / float(self.fps)

    def seed_for(self, index: int, attempt: int = 0) -> int:
        return retry_seed(self.seed_base, index, attempt)

    def boundaries_seconds(self) -> list[float]:
        """Seam positions (in seconds) inside the ASSEMBLED clip.

        One entry per chunk boundary; used to report where the seams are.
        """
        out: list[float] = []
        for chunk in self.chunks[:-1]:
            out.append((chunk.out_last + 1) / float(self.fps))
        return out

    def render_seams_seconds(self) -> list[float]:
        """Seam positions (in seconds) on the RENDER timeline.

        This is the timeline the chunks are rendered on: chunk i+1 starts on
        the last frame of chunk i, so the render timeline is one frame LONGER
        per seam than the assembled clip.

        The audio is cut here, and the 40 ms crossfade compensates the
        difference: each seam drops one frame (41.7 ms at 24 fps) from the
        assembled clip and 40 ms from the audio. The two nearly cancel, so
        the soundtrack stays locked to the picture instead of creeping ahead
        by 40 ms per seam.
        """
        out: list[float] = []
        rendered = 0
        for chunk in self.chunks[:-1]:
            rendered += chunk.render_frames
            out.append(rendered / float(self.fps))
        return out


def plan_chunks(
    total_frames: int,
    chunk_frames: int = DEFAULT_CHUNK_FRAMES,
    seed_base: int = 0,
    fps: int = DEFAULT_FPS,
) -> Plan:
    """Split ``total_frames`` into a chain of chunks.

    The last chunk is shortened so the assembled clip lands as close to
    ``total_frames`` as the H3 length grid allows; the assembly trims the
    remainder. Chunk 0 keeps the full size so the opening of the clip is not
    sacrificed to the tail.
    """
    if total_frames <= 0:
        raise ValueError("total_frames must be positive")
    notes: list[str] = []

    effective = _largest_valid_length_at_most(chunk_frames)
    if effective != chunk_frames:
        notes.append(
            f"chunk size {chunk_frames} snapped down to {effective}: H3 only "
            f"accepts lengths of 17k+5 frames"
        )

    if total_frames <= effective:
        # A single chunk: render a valid length that covers the request.
        render = next_valid_length(total_frames)
        if render != total_frames:
            notes.append(
                f"single chunk renders {render} frames and the assembly trims "
                f"to {total_frames}"
            )
        chunk = ChunkPlan(
            index=0,
            seed=retry_seed(seed_base, 0, 0),
            render_frames=render,
            out_first=0,
            out_last=render - 1,
            start_seconds=0.0,
        )
        return Plan(
            chunks=(chunk,),
            total_frames=total_frames,
            assembled_frames=render,
            chunk_frames_requested=chunk_frames,
            chunk_frames_effective=render,
            fps=fps,
            seed_base=seed_base,
            notes=tuple(notes),
        )

    # How many full chunks are needed?
    # assembled after n chunks = effective + (n - 1) * (effective - 1)
    covered = effective
    count = 1
    while covered < total_frames:
        covered += effective - 1
        count += 1

    chunks: list[ChunkPlan] = []
    assembled = 0
    for index in range(count):
        if index == 0:
            render = effective
        elif index < count - 1:
            render = effective
        else:
            # The last chunk only has to cover what is still missing, plus
            # the one duplicated frame it inherits from its predecessor.
            missing = total_frames - assembled
            render = next_valid_length(missing + 1)
            if render != missing + 1:
                notes.append(
                    f"last chunk renders {render} frames (needs {missing + 1}); "
                    f"the assembly trims the surplus"
                )
        out_first = assembled
        out_last = assembled + render - 1 - (1 if index > 0 else 0)
        chunks.append(
            ChunkPlan(
                index=index,
                seed=retry_seed(seed_base, index, 0),
                render_frames=render,
                out_first=out_first,
                out_last=out_last,
                start_seconds=out_first / float(fps),
            )
        )
        assembled = out_last + 1

    return Plan(
        chunks=tuple(chunks),
        total_frames=total_frames,
        assembled_frames=assembled,
        chunk_frames_requested=chunk_frames,
        chunk_frames_effective=effective,
        fps=fps,
        seed_base=seed_base,
        notes=tuple(notes),
    )


def prompt_hash(prompt_file: str | Path) -> str:
    """Stable short hash of the prompt file's contents.

    The prompt is repeated verbatim in every chunk, so it is also the thing
    that decides whether a cached chunk is still valid: a different prompt
    means a different chunk.

    Only the CONTENT is hashed, not the file name - renaming the prompt
    file must not throw away hours of rendering.
    """
    digest = hashlib.sha256()
    path = Path(prompt_file)
    try:
        with path.open("rb") as handle:
            for block in iter(lambda: handle.read(65536), b""):
                digest.update(block)
    except OSError:
        digest.update(b"<unreadable>")
    return digest.hexdigest()[:16]
