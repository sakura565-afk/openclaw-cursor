# -*- coding: utf-8 -*-
"""The state file: what makes a re-run cheap.

A render of a 175-frame chunk costs minutes of GPU time. A pipeline that
re-renders everything on every invocation is unusable in practice, so the
pipeline writes a JSON state file next to the output and consults it before
every render.

The chunk key is ``index + seed + prompt hash``. All three parts matter:

* the index identifies the position in the chain;
* the seed identifies the take - a chunk rendered with a different seed is a
  different chunk, and a retry legitimately uses a new seed;
* the prompt hash invalidates every chunk when the character description
  changes, because the description is repeated verbatim in every chunk and
  any edit changes what the chunk should look like.

A chunk is reused only when the state says it was accepted AND the rendered
file is still on disk. Both conditions: a state file without the file is
worthless, and a file without the state would be re-rendered.
"""
from __future__ import annotations

import json
import os
import tempfile
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

__all__ = ["STATE_VERSION", "ChunkRecord", "State", "chunk_key"]

STATE_VERSION = 1


def chunk_key(index: int, seed: int, prompt_digest: str) -> str:
    """The state key of one chunk: index + seed + prompt hash."""
    return f"{index}:{seed}:{prompt_digest}"


@dataclass
class ChunkRecord:
    """What is known about one chunk of the chain."""

    index: int
    seed: int
    prompt_hash: str
    accepted: bool = False
    attempts: int = 0
    video: str = ""
    """Path of the rendered chunk file."""
    last_frame: str = ""
    """Path of the PNG that was the LAST frame of this chunk (the next
    chunk's first frame)."""
    anchor: str = ""
    """Path of the frame this chunk was rendered FROM.

    The chain is only valid as long as every chunk was rendered from the
    frame its predecessor ended on. When a chunk is re-rendered, that frame
    changes, and every chunk after it must be rendered again - otherwise
    the clip stitches together takes that never saw each other. The anchor
    is what makes that visible on a re-run.
    """
    metrics: dict[str, Any] = field(default_factory=dict)
    reason: str = ""
    """Why the chunk was rejected, or why it was accepted."""

    @property
    def key(self) -> str:
        return chunk_key(self.index, self.seed, self.prompt_hash)

    def to_json(self) -> dict[str, Any]:
        return {
            "index": self.index,
            "seed": self.seed,
            "prompt_hash": self.prompt_hash,
            "accepted": self.accepted,
            "attempts": self.attempts,
            "video": self.video,
            "last_frame": self.last_frame,
            "anchor": self.anchor,
            "metrics": self.metrics,
            "reason": self.reason,
        }

    @classmethod
    def from_json(cls, data: dict[str, Any]) -> "ChunkRecord":
        return cls(
            index=int(data.get("index", 0)),
            seed=int(data.get("seed", 0)),
            prompt_hash=str(data.get("prompt_hash", "")),
            accepted=bool(data.get("accepted", False)),
            attempts=int(data.get("attempts", 0)),
            video=str(data.get("video", "")),
            last_frame=str(data.get("last_frame", "")),
            anchor=str(data.get("anchor", "")),
            metrics=dict(data.get("metrics") or {}),
            reason=str(data.get("reason", "")),
        )


class State:
    """The pipeline state, persisted as JSON next to the output file."""

    def __init__(self, path: Path, data: dict[str, Any] | None = None) -> None:
        self.path = Path(path)
        self.data: dict[str, Any] = data if data is not None else {
            "version": STATE_VERSION,
            "created": time.time(),
            "updated": time.time(),
            "chunks": {},
            "assembly": {},
        }
        self.data.setdefault("chunks", {})
        self.data.setdefault("assembly", {})

    # ------------------------------------------------------------------ load
    @classmethod
    def load(cls, path: str | Path) -> "State":
        """Load the state, or start an empty one when there is none."""
        path = Path(path)
        if not path.exists():
            return cls(path)
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            # A damaged state file must not stop a render: an empty state
            # means "render everything again", which is correct but slow.
            return cls(path)
        if not isinstance(data, dict):
            return cls(path)
        data.setdefault("chunks", {})
        data.setdefault("assembly", {})
        return cls(path, data)

    def save(self) -> None:
        """Write the state atomically (a crash must not corrupt it)."""
        self.data["updated"] = time.time()
        self.data["version"] = STATE_VERSION
        self.path.parent.mkdir(parents=True, exist_ok=True)
        handle = tempfile.NamedTemporaryFile(
            "w", encoding="utf-8", dir=str(self.path.parent),
            prefix=self.path.name, suffix=".tmp", delete=False,
        )
        try:
            with handle:
                json.dump(self.data, handle, ensure_ascii=False, indent=2)
            os.replace(handle.name, self.path)
        except BaseException:
            try:
                os.unlink(handle.name)
            except OSError:
                pass
            raise

    # ---------------------------------------------------------------- chunks
    def records(self) -> list[ChunkRecord]:
        out: list[ChunkRecord] = []
        for raw in self.data["chunks"].values():
            if isinstance(raw, dict):
                out.append(ChunkRecord.from_json(raw))
        out.sort(key=lambda record: record.index)
        return out

    def accepted_for(
        self,
        index: int,
        prompt_digest: str,
        anchor: str = "",
        seeds: tuple[int, ...] | set[int] = (),
    ) -> ChunkRecord | None:
        """The accepted chunk at ``index`` for this prompt, if there is one.

        The rendered file must still exist: a state file that outlived its
        renders is a stale state, not a cache.

        ``anchor`` is the frame the chunk has to have been rendered from.
        When it does not match, the chunk is re-rendered even though it was
        accepted before - its predecessor changed, so the take it belongs to
        no longer leads to it. The comparison fails closed: a record that
        does not remember its anchor cannot be proven to continue the chain,
        so it is not reused.

        ``seeds`` are the seeds this chunk is allowed to have under the
        current ``--seed-base``. A chunk rendered with another seed belongs
        to another job, even if its index and prompt match - reusing it
        would silently ignore ``--seed-base``. An empty tuple means the
        caller does not constrain the seed.
        """
        for record in self.records():
            if record.index != index or not record.accepted:
                continue
            if record.prompt_hash != prompt_digest:
                continue
            if seeds and record.seed not in seeds:
                continue
            if not record.video or not Path(record.video).exists():
                continue
            if anchor:
                if not record.anchor or record.anchor != anchor:
                    continue
            return record
        return None

    def put(self, record: ChunkRecord) -> None:
        """Store a chunk record under its key."""
        self.data["chunks"][record.key] = record.to_json()
        self.save()

    def set_assembly(self, payload: dict[str, Any]) -> None:
        self.data["assembly"] = payload
        self.save()
