"""Tests for the ComfyUI UI-workflow to API-prompt converter."""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from scripts.comfy_ui_to_api import _input_kind, _schema_widget_names, apply_overrides, convert


def _schema() -> dict:
    return {
        "Demo": {
            "input": {
                "required": {
                    "model": ["MODEL", {}],
                    "prompt": ["STRING", {"multiline": True}],
                    "width": ["INT", {"default": 512}],
                    "mode": [["a", "b"], {"default": "a"}],
                    "images": ["IMAGE", {}],
                },
                "optional": {
                    "flag": ["BOOLEAN", {"default": False}],
                    "extra": ["CLIP_VISION", {}],
                },
            }
        }
    }


def test_input_kind_distinguishes_combo_from_primitive() -> None:
    assert _input_kind(["STRING", {}]) == "STRING"
    assert _input_kind([["a", "b"], {}]) == "COMBO"


def test_widget_names_skip_data_inputs() -> None:
    names = _schema_widget_names(_schema(), "Demo", set())
    # data inputs (MODEL, IMAGE, CLIP_VISION) have no widget
    assert names == ["prompt", "width", "mode", "flag"]


def test_convert_maps_widget_values_by_name() -> None:
    workflow = {
        "nodes": [
            {
                "id": 1,
                "type": "Demo",
                "inputs": [{"name": "model", "type": "MODEL", "link": 7}],
                "widgets_values": ["hello", 512, "b", True],
            },
            {"id": 2, "type": "Note", "inputs": [], "widgets_values": ["ignore me"]},
        ],
        "links": [[7, 2, 0, 1, 0, "MODEL"]],
    }
    prompt = convert(workflow, _schema())
    assert "2" not in prompt, "Note nodes must not become graph nodes"
    node = prompt["1"]
    assert node["class_type"] == "Demo"
    assert node["inputs"]["prompt"] == "hello"
    assert node["inputs"]["width"] == 512
    assert node["inputs"]["mode"] == "b"
    assert node["inputs"]["flag"] is True
    # the linked input wins over any widget value
    assert node["inputs"]["model"] == ["2", 0]


def test_convert_without_schema_falls_back_to_generic_names() -> None:
    workflow = {
        "nodes": [{"id": 1, "type": "Demo", "inputs": [], "widgets_values": [1, 2]}],
        "links": [],
    }
    prompt = convert(workflow, None)
    assert prompt["1"]["inputs"]["widget_0"] == 1
    assert prompt["1"]["inputs"]["widget_1"] == 2


def test_apply_overrides_casts_values() -> None:
    prompt = {"1": {"class_type": "Demo", "inputs": {"width": 512}}}
    prompt = apply_overrides(prompt, ["1.width=768", "1.flag=true", "1.name=abc"])
    assert prompt["1"]["inputs"]["width"] == 768
    assert prompt["1"]["inputs"]["flag"] is True
    assert prompt["1"]["inputs"]["name"] == "abc"


def test_apply_overrides_rejects_unknown_node() -> None:
    with pytest.raises(SystemExit):
        apply_overrides({"1": {"class_type": "Demo", "inputs": {}}}, ["9.width=1"])


def test_face_refine_example_workflow_converts() -> None:
    """The shipped H3 face-refine example has to survive the conversion."""
    example = Path(
        os.environ.get("H3_FACEREFINE_EXAMPLE", "")
        or os.path.join(
            os.environ.get("COMFY_ROOT", ""),
            "custom_nodes", "ComfyUI-H3-FaceRefine", "example_workflows",
            "H3_Face_Refine_Auto_Select.json",
        )
    )
    if not example.exists():
        pytest.skip("face-refine example workflow is not installed")
    workflow = json.loads(example.read_text(encoding="utf-8"))
    prompt = convert(workflow, _schema())
    assert "H3FaceTrackCrop" in {n["class_type"] for n in prompt.values()}
    assert "H3FaceStitch" in {n["class_type"] for n in prompt.values()}
