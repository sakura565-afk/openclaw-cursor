#!/usr/bin/env python3
"""Verify local Wan2.2 VACE fp8_scaled checkpoints against Comfy-Org digests."""

from __future__ import annotations

import argparse
import hashlib
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

# Official Comfy-Org/Wan_2.2_ComfyUI_Repackaged LFS digests (2026-10).
# https://huggingface.co/Comfy-Org/Wan_2.2_ComfyUI_Repackaged/tree/main/split_files/diffusion_models
WAN_VACE_FP8_SCALED_CHECKPOINTS: dict[str, tuple[str, int]] = {
    "wan2.2_fun_vace_high_noise_14B_fp8_scaled.safetensors": (
        "23130f30207f6c8697a5bead113ddc9904cfbb5e8fa51a550df3cee4d21fb54c",
        17_346_056_104,
    ),
    "wan2.2_fun_vace_low_noise_14B_fp8_scaled.safetensors": (
        "ca55a5cc543e28576edbd2079f8c1de05f71cd9e85bf96fab635f55663dbcb69",
        17_346_056_104,
    ),
}

HF_REPACK_BASE = (
    "https://huggingface.co/Comfy-Org/Wan_2.2_ComfyUI_Repackaged/resolve/main/"
    "split_files/diffusion_models"
)


@dataclass(frozen=True)
class CheckpointCheckResult:
    name: str
    path: Path
    ok: bool
    message: str
    size: int | None = None
    sha256: str | None = None


def stream_sha256(path: Path, chunk: int = 1 << 20) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while True:
            block = handle.read(chunk)
            if not block:
                break
            digest.update(block)
    return digest.hexdigest()


def default_models_dir() -> Path:
    return Path("ComfyUI") / "models" / "diffusion_models"


def resolve_checkpoint_path(name: str, models_dir: Path) -> Path:
    return models_dir / name


def verify_checkpoint_file(
    path: Path,
    *,
    expected_sha256: str,
    expected_size: int,
    compute_hash: bool = True,
) -> CheckpointCheckResult:
    name = path.name
    if not path.is_file():
        return CheckpointCheckResult(
            name=name,
            path=path,
            ok=False,
            message="file not found",
        )

    size = path.stat().st_size
    if size != expected_size:
        return CheckpointCheckResult(
            name=name,
            path=path,
            ok=False,
            message=f"size mismatch: got {size}, expected {expected_size}",
            size=size,
        )

    if not compute_hash:
        return CheckpointCheckResult(
            name=name,
            path=path,
            ok=True,
            message="size OK (hash skipped)",
            size=size,
        )

    file_hash = stream_sha256(path)
    if file_hash.lower() != expected_sha256.lower():
        return CheckpointCheckResult(
            name=name,
            path=path,
            ok=False,
            message=f"sha256 mismatch: got {file_hash}, expected {expected_sha256}",
            size=size,
            sha256=file_hash,
        )

    return CheckpointCheckResult(
        name=name,
        path=path,
        ok=True,
        message="OK",
        size=size,
        sha256=file_hash,
    )


def verify_all(
    models_dir: Path,
    *,
    names: Iterable[str] | None = None,
    compute_hash: bool = True,
) -> list[CheckpointCheckResult]:
    selected = names or WAN_VACE_FP8_SCALED_CHECKPOINTS.keys()
    results: list[CheckpointCheckResult] = []
    for name in selected:
        expected_sha, expected_size = WAN_VACE_FP8_SCALED_CHECKPOINTS[name]
        path = resolve_checkpoint_path(name, models_dir)
        results.append(
            verify_checkpoint_file(
                path,
                expected_sha256=expected_sha,
                expected_size=expected_size,
                compute_hash=compute_hash,
            )
        )
    return results


def _main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Verify Wan2.2 VACE fp8_scaled diffusion checkpoints "
            "(Comfy-Org official SHA-256 and size)."
        )
    )
    parser.add_argument(
        "--models-dir",
        type=Path,
        default=default_models_dir(),
        help="Directory containing the .safetensors files (default: ComfyUI/models/diffusion_models).",
    )
    parser.add_argument(
        "--size-only",
        action="store_true",
        help="Check file size only (fast; skips SHA-256 streaming).",
    )
    parser.add_argument(
        "--print-urls",
        action="store_true",
        help="Print Hugging Face resolve URLs for re-download with scripts/dl_verified.py.",
    )
    args = parser.parse_args(argv)

    if args.print_urls:
        for name, (sha, size) in WAN_VACE_FP8_SCALED_CHECKPOINTS.items():
            url = f"{HF_REPACK_BASE}/{name}"
            print(f"# {name}  size={size}  sha256={sha}")
            print(
                f"python scripts/dl_verified.py {url} "
                f"--dest {args.models_dir / name} "
                f"--sha256 {sha} --expect-size {size} --kind hf"
            )
        return 0

    results = verify_all(args.models_dir, compute_hash=not args.size_only)
    failed = False
    for row in results:
        status = "OK" if row.ok else "FAIL"
        detail = row.message
        if row.sha256:
            detail = f"{detail} sha256={row.sha256}"
        elif row.size is not None and row.ok:
            detail = f"{detail} size={row.size}"
        print(f"{status}: {row.path} — {detail}")
        if not row.ok:
            failed = True

    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(_main())
