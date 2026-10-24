#!/usr/bin/env python3
"""Verify local Wan2.2 VACE fp8_scaled checkpoints against Comfy-Org digests."""

from __future__ import annotations

import argparse
import hashlib
import json
import struct
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

MAX_SAFETENSORS_HEADER_BYTES = 100 * 1024 * 1024


@dataclass(frozen=True)
class CheckpointCheckResult:
    name: str
    path: Path
    ok: bool
    message: str
    size: int | None = None
    sha256: str | None = None


@dataclass(frozen=True)
class SafetensorsHeaderProbeResult:
    ok: bool
    message: str
    file_size: int | None = None
    expected_file_size: int | None = None
    payload_end: int | None = None


def probe_safetensors_header(path: Path) -> SafetensorsHeaderProbeResult:
    """Check header_start + max(data_offsets[1]) == file_size (local integrity probe)."""
    if not path.is_file():
        return SafetensorsHeaderProbeResult(
            ok=False,
            message="file not found",
        )

    file_size = path.stat().st_size
    if file_size < 8:
        return SafetensorsHeaderProbeResult(
            ok=False,
            message="safetensors header probe: file shorter than 8-byte length prefix",
            file_size=file_size,
        )

    with path.open("rb") as handle:
        header_length = struct.unpack("<Q", handle.read(8))[0]
        if header_length == 0 or header_length > MAX_SAFETENSORS_HEADER_BYTES:
            return SafetensorsHeaderProbeResult(
                ok=False,
                message=f"safetensors header probe: invalid header length {header_length}",
                file_size=file_size,
            )
        header_start = 8 + header_length
        if header_start > file_size:
            return SafetensorsHeaderProbeResult(
                ok=False,
                message=(
                    "safetensors header probe: truncated header "
                    f"(need {header_start} bytes, file has {file_size})"
                ),
                file_size=file_size,
                expected_file_size=header_start,
            )
        raw_header = handle.read(header_length)

    if len(raw_header) != header_length:
        return SafetensorsHeaderProbeResult(
            ok=False,
            message="safetensors header probe: truncated header JSON",
            file_size=file_size,
        )

    try:
        header = json.loads(raw_header.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        return SafetensorsHeaderProbeResult(
            ok=False,
            message=f"safetensors header probe: header is not JSON ({exc})",
            file_size=file_size,
        )

    if not isinstance(header, dict):
        return SafetensorsHeaderProbeResult(
            ok=False,
            message="safetensors header probe: header is not a JSON object",
            file_size=file_size,
        )

    max_data_end = 0
    for key, entry in header.items():
        if key == "__metadata__":
            continue
        if not isinstance(entry, dict):
            return SafetensorsHeaderProbeResult(
                ok=False,
                message=f"safetensors header probe: invalid tensor entry for {key!r}",
                file_size=file_size,
            )
        offsets = entry.get("data_offsets")
        if not isinstance(offsets, (list, tuple)) or len(offsets) != 2:
            return SafetensorsHeaderProbeResult(
                ok=False,
                message=f"safetensors header probe: missing data_offsets for {key!r}",
                file_size=file_size,
            )
        try:
            end = int(offsets[1])
        except (TypeError, ValueError):
            return SafetensorsHeaderProbeResult(
                ok=False,
                message=f"safetensors header probe: bad data_offsets end for {key!r}",
                file_size=file_size,
            )
        max_data_end = max(max_data_end, end)

    expected_file_size = header_start + max_data_end
    if file_size != expected_file_size:
        return SafetensorsHeaderProbeResult(
            ok=False,
            message=(
                "safetensors header probe: payload extent mismatch "
                f"(expected file size {expected_file_size} from header, actual {file_size}; "
                f"payload end offset {max_data_end}, header starts at byte {header_start})"
            ),
            file_size=file_size,
            expected_file_size=expected_file_size,
            payload_end=max_data_end,
        )

    return SafetensorsHeaderProbeResult(
        ok=True,
        message="safetensors header probe OK",
        file_size=file_size,
        expected_file_size=expected_file_size,
        payload_end=max_data_end,
    )


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
    run_header_probe: bool = True,
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

    if run_header_probe:
        probe = probe_safetensors_header(path)
        if not probe.ok:
            return CheckpointCheckResult(
                name=name,
                path=path,
                ok=False,
                message=probe.message,
                size=size,
            )

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
    run_header_probe: bool = True,
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
                run_header_probe=run_header_probe,
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
        "--header-probe",
        action="store_true",
        help=(
            "Run only the local safetensors header/payload extent probe "
            "(no official size or SHA-256 check)."
        ),
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

    if args.header_probe:
        failed = False
        for name in WAN_VACE_FP8_SCALED_CHECKPOINTS:
            path = resolve_checkpoint_path(name, args.models_dir)
            probe = probe_safetensors_header(path)
            status = "OK" if probe.ok else "FAIL"
            print(f"{status}: {path} — {probe.message}")
            if not probe.ok:
                failed = True
        return 1 if failed else 0

    results = verify_all(
        args.models_dir,
        compute_hash=not args.size_only,
    )
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
