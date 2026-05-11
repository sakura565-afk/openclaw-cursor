#!/usr/bin/env python3
"""Repo-root entrypoint for the conversation pattern extractor."""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.conversation_extractor import main  # noqa: E402

if __name__ == "__main__":
    raise SystemExit(main())
