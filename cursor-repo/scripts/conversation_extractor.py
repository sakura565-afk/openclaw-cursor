#!/usr/bin/env python3
"""CLI entry shim: canonical implementation is ``scripts/conversation_extractor.py``."""

from __future__ import annotations

import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[2]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from scripts.conversation_extractor import main  # noqa: E402

if __name__ == "__main__":
    raise SystemExit(main())
