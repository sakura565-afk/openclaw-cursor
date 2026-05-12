#!/usr/bin/env python3
"""Cron entry point: run from the repository root (see ``scripts/auto_reflection.py``)."""

from __future__ import annotations

import sys
from pathlib import Path

if __name__ == "__main__":
    _repo = Path(__file__).resolve().parent
    if str(_repo) not in sys.path:
        sys.path.insert(0, str(_repo))
    from scripts.auto_reflection import main

    raise SystemExit(main())
