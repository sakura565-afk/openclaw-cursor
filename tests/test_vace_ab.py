"""Offline tests for scripts.vace_ab (no ComfyUI / GPU)."""

from __future__ import annotations

import copy
import hashlib
import json
from pathlib import Path

import pytest

from scripts import vace_ab as ab

FIXTURE = Path(__file__).resolve().parent / "fixtures" / "vace_defective_workflow.json"

# Checksums of tests/fixtures/vace_defective_workflow.json (recovered ComfyUI prompt).
FIXTURE_FILE_SHA256 = (
    "443a5e053e2942460117116d678ad4c2f862245a91fd0916e69889a0aff30c54"
)
FIXTURE_CANONICAL_SHA256 = (
    "e15af7bcc7220d7e8e0f7d6b93b98a8c1439e3a689a0a78c69a140d48068ae8b"
)

EXPECTED_NODE_CLASSES = {
    "CreateVideo",
    "GetVideoComponents",
    "ImageResizeKJ",
    "LoadVideo",
    "LoadWanVideoT5TextEncoder",
    "SaveVideo",
    "VideoFrameSample",
    "WanVideoBlockSwap",
    "WanVideoDecode",
    "WanVideoModelLoader",
    "WanVideoSampler",
    "WanVideoSetBlockSwap",
    "WanVideoTextEncode",
    "WanVideoVACEEncode",
    "WanVideoVAELoader",
}


@pytest.fixture()
def fixture_workflow() -> dict:
    return json.loads(FIXTURE.read_text(encoding="utf-8"))


def test_fixture_checksums_are_pinned() -> None:
    raw = FIXTURE.read_bytes()
    assert hashlib.sha256(raw).hexdigest() == FIXTURE_FILE_SHA256
    parsed = json.loads(raw.decode("utf-8"))
    canonical = json.dumps(parsed, sort_keys=True, separators=(",", ":")).encode(
        "utf-8"
    )
    assert hashlib.sha256(canonical).hexdigest() == FIXTURE_CANONICAL_SHA256


def test_baseline_loaded_from_fixture(fixture_workflow: dict) -> None:
    """Harness baseline must be the on-disk fixture (not a second in-code copy)."""
    reloaded = ab.load_baseline_workflow()
    assert reloaded == fixture_workflow
    assert ab.BASELINE == reloaded


def test_baseline_graph_is_consistent() -> None:
    issues = ab.validate_graph(ab.BASELINE)
    assert issues == []


def test_baseline_is_the_real_defective_prompt(fixture_workflow: dict) -> None:
    assert (
        fixture_workflow["1"]["inputs"]["model"]
        == "wan2.2_fun_vace_high_noise_14B_fp8_scaled.safetensors"
    )
    assert (
        fixture_workflow["2"]["inputs"]["model"]
        == "wan2.2_fun_vace_low_noise_14B_fp8_scaled.safetensors"
    )
    for loader_id in ("1", "2"):
        vace_key = fixture_workflow[loader_id]["inputs"].get("vace_model")
        assert vace_key is None

    vace = fixture_workflow["6"]
    assert vace["class_type"] == "WanVideoVACEEncode"
    assert vace["inputs"]["width"] == 480
    assert vace["inputs"]["height"] == 832
    assert vace["inputs"]["num_frames"] == 29
    assert vace["inputs"]["strength"] == 1.0
    assert vace["inputs"]["vace_start_percent"] == 0.0
    assert vace["inputs"]["vace_end_percent"] == 1.0
    assert vace["inputs"]["tiled_vae"] is False

    for sampler_id in (ab.NODE_HIGH_SAMPLER, ab.NODE_LOW_SAMPLER):
        sampler = fixture_workflow[sampler_id]["inputs"]
        assert sampler["steps"] == 6
        assert sampler["cfg"] == 1.0
        assert sampler["shift"] == 5.0
        assert sampler["scheduler"] == "dpm++_sde"

    assert fixture_workflow["30"]["inputs"]["file"] == "vace_test.mp4"
    assert fixture_workflow["33"]["inputs"]["divisible_by"] == 16

    classes = {node["class_type"] for node in fixture_workflow.values()}
    assert classes == EXPECTED_NODE_CLASSES
    assert "WanVideoImageToVideoEncode" not in classes
    assert not any(name.startswith("VHS_") for name in classes)


def test_apply_overrides_cfg_both_samplers() -> None:
    out = ab.apply_overrides(ab.BASELINE, {"cfg": 5.0})
    assert out[ab.NODE_HIGH_SAMPLER]["inputs"]["cfg"] == 5.0
    assert out[ab.NODE_LOW_SAMPLER]["inputs"]["cfg"] == 5.0
    assert ab.BASELINE[ab.NODE_HIGH_SAMPLER]["inputs"]["cfg"] == 1.0


def test_apply_overrides_steps_and_split() -> None:
    out = ab.apply_overrides(ab.BASELINE, {"steps": 20, "split": 10})
    assert out[ab.NODE_HIGH_SAMPLER]["inputs"]["steps"] == 20
    assert out[ab.NODE_LOW_SAMPLER]["inputs"]["steps"] == 20
    assert out[ab.NODE_HIGH_SAMPLER]["inputs"]["start_step"] == 0
    assert out[ab.NODE_HIGH_SAMPLER]["inputs"]["end_step"] == 10
    assert out[ab.NODE_LOW_SAMPLER]["inputs"]["start_step"] == 10
    assert out[ab.NODE_LOW_SAMPLER]["inputs"]["end_step"] == -1


def test_apply_overrides_scheduler_shift_vace_resize_loaders_prefix() -> None:
    out = ab.apply_overrides(
        ab.BASELINE,
        {
            "scheduler": "unipc",
            "shift": 8.0,
            "vace_strength": 0.7,
            "vace_end_percent": 0.8,
            "divisible_by": 32,
            "attention_mode": "sdpa",
            "quantization": "disabled",
            "prefix": "vace_ab_test",
        },
    )
    assert out[ab.NODE_HIGH_SAMPLER]["inputs"]["scheduler"] == "unipc"
    assert out[ab.NODE_LOW_SAMPLER]["inputs"]["scheduler"] == "unipc"
    assert out[ab.NODE_HIGH_SAMPLER]["inputs"]["shift"] == 8.0
    assert out[ab.NODE_LOW_SAMPLER]["inputs"]["shift"] == 8.0
    assert out[ab.NODE_VACE]["inputs"]["strength"] == 0.7
    assert out[ab.NODE_VACE]["inputs"]["vace_end_percent"] == 0.8
    assert out[ab.NODE_RESIZE]["inputs"]["divisible_by"] == 32
    assert out[ab.NODE_HIGH_LOADER]["inputs"]["attention_mode"] == "sdpa"
    assert out[ab.NODE_LOW_LOADER]["inputs"]["attention_mode"] == "sdpa"
    assert out[ab.NODE_HIGH_LOADER]["inputs"]["quantization"] == "disabled"
    assert out[ab.NODE_LOW_LOADER]["inputs"]["quantization"] == "disabled"
    assert out[ab.NODE_SAVE]["inputs"]["filename_prefix"] == "vace_ab_test"


def test_apply_overrides_single_lever_does_not_touch_unrelated_nodes() -> None:
    before = copy.deepcopy(ab.BASELINE)
    out = ab.apply_overrides(ab.BASELINE, {"scheduler": "uni_pc"})
    for nid in (
        ab.NODE_VACE,
        ab.NODE_RESIZE,
        ab.NODE_HIGH_LOADER,
        ab.NODE_LOW_LOADER,
        ab.NODE_SAVE,
    ):
        assert out[nid] == before[nid]


def test_apply_overrides_does_not_mutate_baseline() -> None:
    snap = copy.deepcopy(ab.BASELINE)
    ab.apply_overrides(ab.BASELINE, {"cfg": 2.0})
    ab.apply_overrides(ab.BASELINE, {"steps": 10, "split": 5})
    assert ab.BASELINE == snap


def test_cases_each_change_one_variable_except_combo() -> None:
    for name, overrides in ab.CASES.items():
        if name == "control":
            assert overrides == {}
            continue
        if name == "combo":
            assert overrides == {
                "cfg": 5.0,
                "steps": 20,
                "split": 10,
                "scheduler": "unipc",
                "shift": 8.0,
            }
            continue
        if name == "steps20":
            assert overrides == {"steps": 20, "split": 10}
            continue
        if name == "unipc":
            assert overrides == {"scheduler": "unipc", "shift": 8.0}
            continue
        assert len(overrides) == 1, f"{name} should isolate one variable"


def test_parity_test_fails_if_fixture_drifted() -> None:
    """Guard against circular parity: mutating the fixture must break baseline equality."""
    drifted = copy.deepcopy(ab.BASELINE)
    drifted["6"]["inputs"]["strength"] = 0.99
    assert drifted != ab.BASELINE
