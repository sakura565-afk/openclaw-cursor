#!/usr/bin/env python3
"""Extract structured session signals (decisions, errors, tools, learnings) for self-improvement.

Runs the same pipeline as ``python -m scripts.conversation_extractor``; kept at repo root
for simple ``python conversation_extractor.py <transcript>`` invocation.
"""

from __future__ import annotations

import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from scripts.conversation_extractor import main  # noqa: E402

if __name__ == "__main__":
    raise SystemExit(main())
