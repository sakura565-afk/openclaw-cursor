"""Offline tests for scripts.vace_ab (no ComfyUI / GPU)."""

from __future__ import annotations

import copy
import json
from pathlib import Path

import pytest

from scripts import vace_ab as ab

FIXTURE = Path(__file__).resolve().parent / "fixtures" / "vace_defective_workflow.json"


@pytest.fixture()
def fixture_workflow() -> dict:
    return json.loads(FIXTURE.read_text(encoding="utf-8"))


def test_baseline_graph_is_consistent() -> None:
    issues = ab.validate_graph(ab.BASELINE)
    assert issues == []


def test_baseline_matches_recovered_workflow(fixture_workflow: dict) -> None:
    assert set(ab.BASELINE) == set(fixture_workflow)
    for nid, node in fixture_workflow.items():
        expected = copy.deepcopy(node)
        actual = copy.deepcopy(ab.BASELINE[nid])
        if nid == ab.NODE_SAVE:
            expected["inputs"].pop("filename_prefix", None)
            actual["inputs"].pop("filename_prefix", None)
        assert actual == expected


def test_apply_overrides_cfg_both_samplers() -> None:
    out = ab.apply_overrides(ab.BASELINE, {"cfg": 3.5})
    assert out[ab.NODE_HIGH_SAMPLER]["inputs"]["cfg"] == 3.5
    assert out[ab.NODE_LOW_SAMPLER]["inputs"]["cfg"] == 3.5
    assert ab.BASELINE[ab.NODE_HIGH_SAMPLER]["inputs"]["cfg"] == 1.0


def test_apply_overrides_steps_and_split() -> None:
    out = ab.apply_overrides(ab.BASELINE, {"steps": 8, "split": 4})
    assert out[ab.NODE_HIGH_SAMPLER]["inputs"]["steps"] == 8
    assert out[ab.NODE_LOW_SAMPLER]["inputs"]["steps"] == 8
    assert out[ab.NODE_HIGH_SAMPLER]["inputs"]["start_step"] == 0
    assert out[ab.NODE_HIGH_SAMPLER]["inputs"]["end_step"] == 4
    assert out[ab.NODE_LOW_SAMPLER]["inputs"]["start_step"] == 4
    assert out[ab.NODE_LOW_SAMPLER]["inputs"]["end_step"] == -1


def test_apply_overrides_scheduler_shift_vace_resize_loaders() -> None:
    out = ab.apply_overrides(
        ab.BASELINE,
        {
            "scheduler": "euler",
            "shift": 5.0,
            "vace_strength": 0.75,
            "vace_end_percent": 0.8,
            "divisible_by": 32,
            "attention_mode": "sdpa",
            "quantization": "fp8_e4m3fn",
        },
    )
    assert out[ab.NODE_HIGH_SAMPLER]["inputs"]["scheduler"] == "euler"
    assert out[ab.NODE_LOW_SAMPLER]["inputs"]["scheduler"] == "euler"
    assert out[ab.NODE_HIGH_SAMPLER]["inputs"]["shift"] == 5.0
    assert out[ab.NODE_LOW_SAMPLER]["inputs"]["shift"] == 5.0
    assert out[ab.NODE_VACE]["inputs"]["strength"] == 0.75
    assert out[ab.NODE_VACE]["inputs"]["vace_end_percent"] == 0.8
    assert out[ab.NODE_RESIZE]["inputs"]["divisible_by"] == 32
    assert out[ab.NODE_HIGH_LOADER]["inputs"]["attention_mode"] == "sdpa"
    assert out[ab.NODE_LOW_LOADER]["inputs"]["attention_mode"] == "sdpa"
    assert out[ab.NODE_HIGH_LOADER]["inputs"]["quantization"] == "fp8_e4m3fn"
    assert out[ab.NODE_LOW_LOADER]["inputs"]["quantization"] == "fp8_e4m3fn"


def test_apply_overrides_single_lever_does_not_touch_unrelated_nodes() -> None:
    before = copy.deepcopy(ab.BASELINE)
    out = ab.apply_overrides(ab.BASELINE, {"scheduler": "uni_pc"})
    for nid in (
        ab.NODE_VACE,
        ab.NODE_RESIZE,
        ab.NODE_HIGH_LOADER,
        ab.NODE_LOW_LOADER,
    ):
        assert out[nid] == before[nid]


def test_apply_overrides_does_not_mutate_baseline() -> None:
    snap = copy.deepcopy(ab.BASELINE)
    ab.apply_overrides(ab.BASELINE, {"cfg": 2.0})
    ab.apply_overrides(ab.BASELINE, {"steps": 10, "split": 5})
    assert ab.BASELINE == snap


def test_cases_each_change_one_variable() -> None:
    for name, overrides in ab.CASES.items():
        if name == "baseline":
            assert overrides == {}
            continue
        assert len(overrides) == 1, f"{name} should isolate one variable"
