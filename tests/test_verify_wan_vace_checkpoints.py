"""Tests for scripts.verify_wan_vace_checkpoints (no network)."""

from __future__ import annotations

import hashlib
from pathlib import Path

from scripts.verify_wan_vace_checkpoints import (
    WAN_VACE_FP8_SCALED_CHECKPOINTS,
    verify_all,
    verify_checkpoint_file,
)


HIGH_NAME = "wan2.2_fun_vace_high_noise_14B_fp8_scaled.safetensors"
HIGH_SHA, HIGH_SIZE = WAN_VACE_FP8_SCALED_CHECKPOINTS[HIGH_NAME]


def test_verify_missing_file(tmp_path: Path) -> None:
    path = tmp_path / HIGH_NAME
    result = verify_checkpoint_file(
        path,
        expected_sha256=HIGH_SHA,
        expected_size=HIGH_SIZE,
    )
    assert not result.ok
    assert "not found" in result.message


def test_verify_size_mismatch(tmp_path: Path) -> None:
    path = tmp_path / "tiny.safetensors"
    path.write_bytes(b"ab")
    result = verify_checkpoint_file(
        path,
        expected_sha256="00" * 32,
        expected_size=3,
    )
    assert not result.ok
    assert "size mismatch" in result.message


def test_verify_hash_mismatch(tmp_path: Path) -> None:
    path = tmp_path / "tiny.safetensors"
    payload = b"wrong-content"
    path.write_bytes(payload)
    result = verify_checkpoint_file(
        path,
        expected_sha256="00" * 32,
        expected_size=len(payload),
    )
    assert not result.ok
    assert "sha256 mismatch" in result.message
    assert result.sha256 is not None


def test_verify_ok_with_matching_hash(tmp_path: Path) -> None:
    path = tmp_path / HIGH_NAME
    # Small stand-in: correct size + hash for a tiny payload, not the real 17 GB file.
    payload = b"vace-checkpoint-test-payload"
    size = len(payload)
    digest = hashlib.sha256(payload).hexdigest()
    path.write_bytes(payload)
    result = verify_checkpoint_file(
        path,
        expected_sha256=digest,
        expected_size=size,
    )
    assert result.ok
    assert result.sha256 == digest


def test_verify_size_only_skips_hash(tmp_path: Path) -> None:
    path = tmp_path / "tiny.safetensors"
    payload = b"any"
    path.write_bytes(payload)
    result = verify_checkpoint_file(
        path,
        expected_sha256="00" * 32,
        expected_size=len(payload),
        compute_hash=False,
    )
    assert result.ok
    assert result.sha256 is None


def test_verify_all_iterates_both_official_names(tmp_path: Path) -> None:
    for name in WAN_VACE_FP8_SCALED_CHECKPOINTS:
        (tmp_path / name).write_bytes(b"stub")

    results = verify_all(tmp_path, compute_hash=False)
    assert len(results) == 2
    assert {r.name for r in results} == set(WAN_VACE_FP8_SCALED_CHECKPOINTS)
    assert all(not r.ok for r in results)
    assert all("size mismatch" in r.message for r in results)
