#!/usr/bin/env python3
"""Stable import path ``scripts.error_learning`` → implementation under ``self_improvement``."""

from __future__ import annotations

from scripts.self_improvement.error_learning import *  # noqa: F403

if __name__ == "__main__":
    import sys

    from scripts.self_improvement.error_learning import main

    raise SystemExit(main())
