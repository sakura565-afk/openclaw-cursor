# -*- coding: utf-8 -*-
"""Production pipeline for long H3 clips that must not drift.

The problem this package solves
-------------------------------
A long H3 clip drifts: the face is fine for the first seconds and then
"walks away" towards the tail. The fix is NOT a guide frame in the middle of
the clip - a guide placed mid-clip only holds about two frames, because the
VAE smooths around it, so the tail keeps floating.

The working recipe is recursive chaining: every chunk starts on a frame the
model produced itself (the last frame of the previous chunk), each chunk has
its own seed, the character description is repeated verbatim, the duplicated
boundary frame is trimmed on concat, and the audio is welded with a 40 ms
equal-power crossfade.

This package is the production form of that recipe. It adds three things over
the one-shot runner in ``scripts/h3_chain_video.py``:

* a plan (``--plan``) that prints the chunk table without touching ComfyUI;
* a per-chunk quality gate with re-render-and-retry;
* a state file that makes a re-run skip every chunk that was already
  accepted and written (renders are expensive - hours each).

Modules
-------
``plan``       chunk arithmetic (pure, no I/O)
``paths``      machine-independent path resolution
``state``      the JSON state file (idempotency)
``render``     staging frames into the ComfyUI input dir and calling the runner
``gates``      face-drift / texture metrics and the accept-reject decision
``assembly``   concat, boundary trim, audio weld, seam metrics
``pipeline``   the CLI that ties it together
"""
from __future__ import annotations

import sys
from pathlib import Path

# The package lives in <repo>/scripts/h3_chain_pipeline. Putting the repo root
# on sys.path lets every module use absolute imports (`scripts.h3_chain_pipeline.x`)
# no matter how the entry point was started - as a module, as a script, or from
# a test. No machine-specific path is written down anywhere.
_REPO_ROOT = Path(__file__).resolve().parents[2]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

__all__ = ["__version__"]
__version__ = "1.0.0"
