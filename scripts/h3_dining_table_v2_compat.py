#!/usr/bin/env python3
"""Backward-compatible wrapper for h3_dining_table_director_v2 cron entries."""

from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

import yaml

from video_pipeline.runner import main as runner_main


def _build_config() -> dict:
    """Build pipeline config from legacy env vars and defaults."""
    source_dir = Path(os.environ.get("H3_SOURCE_DIR", "media/inbound/furniture/"))
    output_dir = Path(os.environ.get("H3_OUTPUT_DIR", "media/out/main/furniture/"))
    telegram_chat = int(os.environ.get("TELEGRAM_CHAT_ID", "25979298"))

    return {
        "project": os.environ.get("H3_PROJECT", "furniture_catalog"),
        "source_dir": str(source_dir),
        "outputs": [
            {"format": "1:1", "motion": "slow_push_in", "duration_sec": 7},
            {"format": "9:16", "motion": "slow_push_in", "duration_sec": 7},
            {"format": "16:9", "motion": "slow_push_in", "duration_sec": 7},
        ],
        "quality_gates": {
            "max_ar_drift": 0.03,
            "max_render_sec": 1800,
            "min_resolution_h": 720,
        },
        "delivery": {
            "telegram_chat": telegram_chat,
            "progress_every_min": 30,
            "caption_template": "{name} {format} ready 7s",
        },
        "recovery": {
            "max_retries": 2,
            "resume_from_state": True,
        },
        "output_dir": str(output_dir),
        "director": "h3",
    }


def main(argv: list[str] | None = None) -> int:
    """Generate temp YAML config and delegate to video_pipeline.runner."""
    config_data = _build_config()
    resume = "--resume" in (argv or sys.argv[1:])
    dry_run = "--dry-run" in (argv or sys.argv[1:])

    with tempfile.NamedTemporaryFile(
        mode="w",
        suffix=".yaml",
        delete=False,
        encoding="utf-8",
    ) as tmp:
        yaml.safe_dump(config_data, tmp)
        config_path = tmp.name

    runner_args = ["--config", config_path]
    if resume:
        runner_args.append("--resume")
    if dry_run:
        runner_args.append("--dry-run")

    return runner_main(runner_args)


if __name__ == "__main__":
    raise SystemExit(main())
