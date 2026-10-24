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
    "9093545970f1a290e33218e53a368c7d9cd033e5a7a8dd60d39d04d1e16bb63d"
)
FIXTURE_CANONICAL_SHA256 = (
    "bc4abde5f1e411bf6d371ac4870dc206d90f58537a9f6cd8569802968b5aceea"
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
    "WanVideoTextEncode",
    "WanVideoVACEEncode",
    "WanVideoVAELoader",
}


@pytest.fixture()
def fixture_workflow() -> dict:
    return json.loads(FIXTURE.read_text(encoding="utf-8"))


def test_fixture_checksums_are_pinned() -> None:
    raw = FIXTURE.read_bytes()
    normalized = raw.replace(b"\r\n", b"\n")
    assert hashlib.sha256(normalized).hexdigest() == FIXTURE_FILE_SHA256
    parsed = json.loads(normalized.decode("utf-8"))
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
    assert fixture_workflow["31"]["inputs"]["strategy"] == "head"
    assert fixture_workflow["31"]["inputs"]["seed"] == 42
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
        if name in ("nb_pf2", "nb_pf4"):
            assert "use_non_blocking" in overrides and "prefetch_blocks" in overrides
            continue
        assert len(overrides) == 1, f"{name} should isolate one variable"


def test_parity_test_fails_if_fixture_drifted() -> None:
    """Guard against circular parity: mutating the fixture must break baseline equality."""
    drifted = copy.deepcopy(ab.BASELINE)
    drifted["6"]["inputs"]["strength"] = 0.99
    assert drifted != ab.BASELINE


def test_parse_execution_metrics_span_and_cached() -> None:
    history = {
        "status": {
            "messages": [
                ["execution_start", {"timestamp": 1_000_000}],
                ["execution_cached", ["4", "5", "6", "9", "30", "31", "32", "33"]],
                ["execution_success", {"timestamp": 1_081_600}],
            ]
        }
    }
    span, cached = ab.parse_execution_metrics(history)
    assert span == pytest.approx(81.6)
    assert cached == ["4", "5", "6", "9", "30", "31", "32", "33"]


def test_block_swap_node_is_wan_video_block_swap(fixture_workflow: dict) -> None:
    node = fixture_workflow[ab.NODE_BLOCK_SWAP]
    assert node["class_type"] == "WanVideoBlockSwap"
    assert fixture_workflow["1"]["inputs"]["block_swap_args"][0] == ab.NODE_BLOCK_SWAP
    assert fixture_workflow["2"]["inputs"]["block_swap_args"][0] == ab.NODE_BLOCK_SWAP


def test_apply_overrides_block_swap_levers() -> None:
    out = ab.apply_overrides(
        ab.BASELINE,
        {
            "use_non_blocking": True,
            "prefetch_blocks": 2,
            "blocks_to_swap": 20,
            "vace_blocks_to_swap": 0,
        },
    )
    bs = out[ab.NODE_BLOCK_SWAP]["inputs"]
    assert bs["use_non_blocking"] is True
    assert bs["prefetch_blocks"] == 2
    assert bs["blocks_to_swap"] == 20
    assert bs["vace_blocks_to_swap"] == 0
    assert ab.BASELINE[ab.NODE_BLOCK_SWAP]["inputs"]["use_non_blocking"] is False


def test_build_prompt_nb_pf2_sets_block_swap_inputs() -> None:
    prompt = ab.build_prompt("nb_pf2")
    bs = prompt[ab.NODE_BLOCK_SWAP]["inputs"]
    assert bs["use_non_blocking"] is True
    assert bs["prefetch_blocks"] == 2


def test_build_prompt_control_leaves_block_swap_baseline() -> None:
    prompt = ab.build_prompt("control")
    assert (
        prompt[ab.NODE_BLOCK_SWAP]["inputs"]
        == ab.BASELINE[ab.NODE_BLOCK_SWAP]["inputs"]
    )


def test_build_prompt_nonblocking_and_prefetch2_cases() -> None:
    nb = ab.build_prompt("nonblocking")
    assert nb[ab.NODE_BLOCK_SWAP]["inputs"]["use_non_blocking"] is True
    assert "prefetch_blocks" not in nb[ab.NODE_BLOCK_SWAP]["inputs"]

    pf = ab.build_prompt("prefetch2")
    assert pf[ab.NODE_BLOCK_SWAP]["inputs"]["prefetch_blocks"] == 2
    assert pf[ab.NODE_BLOCK_SWAP]["inputs"]["use_non_blocking"] is False


def test_cases_block_swap_matrix_definitions() -> None:
    assert ab.CASES["nonblocking"] == {"use_non_blocking": True}
    assert ab.CASES["prefetch2"] == {"prefetch_blocks": 2}
    assert ab.CASES["nb_pf2"] == {
        "use_non_blocking": True,
        "prefetch_blocks": 2,
    }
    assert ab.CASES["nb_pf4"] == {
        "use_non_blocking": True,
        "prefetch_blocks": 4,
    }


def test_parse_execution_metrics_missing_timestamps() -> None:
    history = {"status": {"messages": [["execution_cached", ["4"]]]}}
    span, cached = ab.parse_execution_metrics(history)
    assert span is None
    assert cached == ["4"]
