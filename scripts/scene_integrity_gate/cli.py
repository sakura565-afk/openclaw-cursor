"""Command-line entry point for scene-integrity analysis."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Sequence

try:
    from .gate import analyze_video
except ImportError:  # direct script execution: python scripts/scene_integrity_gate/cli.py
    _PKG_ROOT = str(Path(__file__).resolve().parent.parent.parent)
    if _PKG_ROOT not in sys.path:
        sys.path.insert(0, _PKG_ROOT)
    from scripts.scene_integrity_gate.gate import analyze_video


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Reject clips with abrupt scene cuts or a disappearing reference figure."
    )
    parser.add_argument("--video", required=True, type=Path, help="input video (MP4 or another OpenCV codec)")
    parser.add_argument("--json", required=True, type=Path, dest="json_path", help="output report JSON")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        report = analyze_video(args.video)
    except (OSError, ValueError) as error:
        _parser().error(str(error))
    args.json_path.parent.mkdir(parents=True, exist_ok=True)
    args.json_path.write_text(json.dumps(report, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(f"verdict={report['verdict']} frames={report['frame_count']} video={report['video']}")
    for reason in report["reasons"]:
        print(
            f"  {reason['code']}: frame={reason['frame']} "
            f"jump={float(reason['jump']):.4f} threshold={float(reason['threshold']):.4f}"
        )
    print(f"json={args.json_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
