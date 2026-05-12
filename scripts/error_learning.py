#!/usr/bin/env python3
"""CLI shim: implementation lives in ``error_learning`` at the repository root."""

from __future__ import annotations

import sys
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[1]
_root = str(_REPO_ROOT)
if _root not in sys.path:
    sys.path.insert(0, _root)

from error_learning import *  # noqa: F403

if __name__ == "__main__":
    raise SystemExit(main())
