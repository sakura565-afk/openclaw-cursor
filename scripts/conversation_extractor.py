#!/usr/bin/env python3
"""Shim: delegates to scripts.self_improvement.conversation_extractor (same CLI)."""

import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from scripts.self_improvement.conversation_extractor import main

if __name__ == "__main__":
    raise SystemExit(main())
