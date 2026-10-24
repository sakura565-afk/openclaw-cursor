"""Tests for scripts.verify_wan_vace_checkpoints (no network)."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

from scripts.verify_wan_vace_checkpoints import (
    WAN_VACE_FP8_SCALED_CHECKPOINTS,
    probe_safetensors_header,
    verify_all,
    verify_checkpoint_file,
)

HIGH_NAME = "wan2.2_fun_vace_high_noise_14B_fp8_scaled.safetensors"
HIGH_SHA, HIGH_SIZE = WAN_VACE_FP8_SCALED_CHECKPOINTS[HIGH_NAME]


def _safetensors_bytes(payload: bytes, header_extra: dict | None = None) -> bytes:
    n = len(payload)
    header: dict = {
        "tensor.a": {"dtype": "F16", "shape": [1], "data_offsets": [0, n]},
    }
    if header_extra:
        header.update(header_extra)
    raw = json.dumps(header, ensure_ascii=False).encode("utf-8")
    return len(raw).to_bytes(8, "little", signed=False) + raw + payload


def _write_safetensors(path: Path, payload: bytes, header_extra: dict | None = None) -> None:
    path.write_bytes(_safetensors_bytes(payload, header_extra))


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
    payload = b"abcd"
    _write_safetensors(path, payload)
    result = verify_checkpoint_file(
        path,
        expected_sha256="00" * 32,
        expected_size=len(payload) + 1,
    )
    assert not result.ok
    assert "size mismatch" in result.message


def test_verify_hash_mismatch(tmp_path: Path) -> None:
    path = tmp_path / "tiny.safetensors"
    payload = b"wrong-content"
    _write_safetensors(path, payload)
    file_bytes = path.read_bytes()
    result = verify_checkpoint_file(
        path,
        expected_sha256="00" * 32,
        expected_size=len(file_bytes),
    )
    assert not result.ok
    assert "sha256 mismatch" in result.message
    assert result.sha256 is not None


def test_verify_ok_with_matching_hash(tmp_path: Path) -> None:
    path = tmp_path / HIGH_NAME
    payload = b"vace-checkpoint-test-payload"
    _write_safetensors(path, payload)
    file_bytes = path.read_bytes()
    digest = hashlib.sha256(file_bytes).hexdigest()
    result = verify_checkpoint_file(
        path,
        expected_sha256=digest,
        expected_size=len(file_bytes),
    )
    assert result.ok
    assert result.sha256 == digest


def test_verify_size_only_skips_hash(tmp_path: Path) -> None:
    path = tmp_path / "tiny.safetensors"
    payload = b"any"
    _write_safetensors(path, payload)
    file_bytes = path.read_bytes()
    result = verify_checkpoint_file(
        path,
        expected_sha256="00" * 32,
        expected_size=len(file_bytes),
        compute_hash=False,
    )
    assert result.ok
    assert result.sha256 is None


def test_verify_all_iterates_both_official_names(tmp_path: Path) -> None:
    for name in WAN_VACE_FP8_SCALED_CHECKPOINTS:
        _write_safetensors(tmp_path / name, b"stub")

    results = verify_all(tmp_path, compute_hash=False)
    assert len(results) == 2
    assert {r.name for r in results} == set(WAN_VACE_FP8_SCALED_CHECKPOINTS)
    assert all(not r.ok for r in results)
    assert all("size mismatch" in r.message for r in results)


def test_header_probe_accepts_well_formed_safetensors(tmp_path: Path) -> None:
    path = tmp_path / "good.safetensors"
    payload = b"x" * 64
    _write_safetensors(
        path,
        payload,
        header_extra={
            "tensor.b": {"dtype": "F16", "shape": [2], "data_offsets": [32, 64]},
        },
    )
    probe = probe_safetensors_header(path)
    assert probe.ok
    assert probe.file_size == probe.expected_file_size


def test_header_probe_rejects_truncated_payload(tmp_path: Path) -> None:
    payload = b"a" * 100
    header = {
        "tensor.a": {"dtype": "F16", "shape": [1], "data_offsets": [0, 100]},
    }
    raw = json.dumps(header).encode("utf-8")
    # Header claims 100 bytes of payload but only 40 are present.
    path = tmp_path / "truncated.safetensors"
    path.write_bytes(len(raw).to_bytes(8, "little") + raw + payload[:40])

    probe = probe_safetensors_header(path)
    assert not probe.ok
    assert "payload extent mismatch" in probe.message
    assert probe.expected_file_size is not None
    assert probe.file_size is not None
    assert probe.expected_file_size > probe.file_size


def test_header_probe_rejects_duplicated_interior_chunk(tmp_path: Path) -> None:
    """Mirrors the Wan VACE corruption: splice a duplicate chunk; header still valid."""
    payload = bytes(range(256)) * 4  # 1 KiB patterned payload
    good = _safetensors_bytes(payload)
    insert_at = 200
    chunk_len = 48
    chunk = good[insert_at : insert_at + chunk_len]
    corrupt = good[:insert_at] + chunk + good[insert_at:]

    path = tmp_path / "duplicated-chunk.safetensors"
    path.write_bytes(corrupt)

    probe = probe_safetensors_header(path)
    assert not probe.ok
    assert "payload extent mismatch" in probe.message
    assert probe.file_size is not None
    assert probe.expected_file_size is not None
    assert probe.file_size > probe.expected_file_size


def test_verify_checkpoint_runs_header_probe_before_hash(tmp_path: Path) -> None:
    path = tmp_path / "corrupt.safetensors"
    good = _safetensors_bytes(b"ok")
    path.write_bytes(good + b"extra-bytes-after-valid-end")

    result = verify_checkpoint_file(
        path,
        expected_sha256=hashlib.sha256(path.read_bytes()).hexdigest(),
        expected_size=path.stat().st_size,
    )
    assert not result.ok
    assert "header probe" in result.message
    assert "sha256 mismatch" not in result.message
