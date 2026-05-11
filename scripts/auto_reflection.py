#!/usr/bin/env python3
"""Backward-compatible entry: ``python -m scripts.auto_reflection`` → root ``auto_reflection``."""

from __future__ import annotations

import sys
from pathlib import Path

_pkg_root = Path(__file__).resolve().parent.parent
if str(_pkg_root) not in sys.path:
    sys.path.insert(0, str(_pkg_root))

from auto_reflection import main  # noqa: E402

if __name__ == "__main__":
    raise SystemExit(main())
