"""CLI for calibration, independent validation, and archived-render rechecks."""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]

try:  # package import
    from .calibration import write_blind_report
    from .renders import write_render_report
    from .validation import write_validation_report
except ImportError:  # direct `python scripts/skin_metric_shape/cli.py`
    if str(REPO_ROOT) not in sys.path:
        sys.path.insert(0, str(REPO_ROOT))
    from scripts.skin_metric_shape.calibration import write_blind_report
    from scripts.skin_metric_shape.renders import write_render_report
    from scripts.skin_metric_shape.validation import write_validation_report

DEFAULT_BLIND_ROOT = REPO_ROOT / "scripts" / "skin_metric_groundtruth" / "artifacts" / "blind"
DEFAULT_OUT_DIR = Path(__file__).resolve().parent / "artifacts"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("calibrate", "validate", "recheck", "all"))
    parser.add_argument("--blind-root", type=Path, default=DEFAULT_BLIND_ROOT)
    parser.add_argument("--out-dir", type=Path, default=DEFAULT_OUT_DIR)
    parser.add_argument("--render-root", type=Path, default=None)
    parser.add_argument("--work-dir", type=Path, default=None)
    parser.add_argument("--frames", type=int, default=12)
    args = parser.parse_args(argv)

    if args.command in {"calibrate", "all"}:
        for path in write_blind_report(args.blind_root, args.out_dir):
            print(path)
    if args.command in {"validate", "all"}:
        for path in write_validation_report(args.out_dir):
            print(path)
    if args.command in {"recheck", "all"}:
        configured = args.render_root or os.environ.get("HERMES_RENDER_ROOT", "").strip()
        if not configured:
            parser.error("recheck requires --render-root or HERMES_RENDER_ROOT")
        render_root = Path(configured)
        if not render_root.is_dir():
            parser.error("render root is not a directory")
        for path in write_render_report(
            render_root,
            args.out_dir,
            work_dir=args.work_dir,
            frames=args.frames,
        ):
            print(path)
    return 0
