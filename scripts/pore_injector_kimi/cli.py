"""Command-line interface for the CPU-only pore injector."""

from __future__ import annotations

import argparse
import json

from .pore_injector import process_video


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Inject sparse dark pore pits with light rims into a video."
    )
    parser.add_argument("--video", required=True, help="input video")
    parser.add_argument("--out", required=True, help="output MP4; JSON uses the same stem")
    parser.add_argument("--strength", type=float, default=2.0)
    parser.add_argument("--pit-density", type=float, default=0.003)
    parser.add_argument("--rim", type=float, default=0.35)
    parser.add_argument("--pore-sigma", type=float, default=1.25)
    parser.add_argument("--threshold", type=float, default=0.008)
    parser.add_argument("--mask", default=None, help="optional uint8 grayscale skin PNG")
    parser.add_argument("--temporal-sigma", type=float, default=1.5)
    parser.add_argument("--seed", type=int, default=0)
    parser.add_argument(
        "--two-octave",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="combine fine pores with a sparser broad octave (default: enabled)",
    )
    parser.add_argument("--max-frames", type=int, default=None)
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        report = process_video(**vars(args))
    except (ValueError, RuntimeError) as exc:
        parser.error(str(exc))
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
