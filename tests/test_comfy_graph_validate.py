"""Regression tests for static ComfyUI API graph validation."""
from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path
import urllib.request

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from scripts import comfy_graph_validate as graph_validator
from scripts.comfy_graph_validate import ComfyGraphValidator


OBJECT_INFO = {
    "ImageSource": {
        "input": {"required": {}},
        "output": ["IMAGE"],
    },
    "TypeTarget": {
        "input": {
            "required": {
                "number": ["INT", {}],
                "image": ["IMAGE", {}],
            }
        },
        "output": ["IMAGE"],
    },
    "MiniMaxH3ImageConditioning": {
        "input": {
            "required": {
                "images": [
                    "COMFY_AUTOGROW_V3",
                    {"prefix": "image_", "min": 1},
                ],
            }
        },
        "output": ["CONDITIONING"],
    },
}


def validate(graph: dict):
    return ComfyGraphValidator().validate_graph(graph, OBJECT_INFO)


def codes(graph: dict) -> list[str]:
    return [issue.code for issue in validate(graph)]


def test_autogrow_rejects_plain_list_of_links() -> None:
    """Regression: a batched reference list is not an AUTOGROW input."""
    graph = {
        "11": {"class_type": "ImageSource", "inputs": {}},
        "12": {"class_type": "ImageSource", "inputs": {}},
        "3": {
            "class_type": "MiniMaxH3ImageConditioning",
            "inputs": {"images": [["11", 0], ["12", 0]]},
        },
    }

    assert codes(graph) == ["dynamic_input_requires_named_slots"]


def test_unknown_class_type_is_reported() -> None:
    graph = {"3": {"class_type": "NoSuchNode", "inputs": {}}}

    assert codes(graph) == ["unknown_class_type"]


def test_missing_required_input_is_reported() -> None:
    graph = {"3": {"class_type": "MiniMaxH3ImageConditioning", "inputs": {}}}

    assert codes(graph) == ["missing_required_input"]


def test_undeclared_input_is_reported() -> None:
    graph = {
        "11": {"class_type": "ImageSource", "inputs": {}},
        "3": {
            "class_type": "MiniMaxH3ImageConditioning",
            "inputs": {"images.image_1": ["11", 0], "typo": 1},
        }
    }

    assert codes(graph) == ["unknown_input"]


def test_link_to_missing_node_is_reported() -> None:
    graph = {
        "3": {
            "class_type": "MiniMaxH3ImageConditioning",
            "inputs": {"images.image_1": ["99", 0]},
        }
    }

    assert codes(graph) == ["dangling_link"]


def test_link_output_index_outside_source_outputs_is_reported() -> None:
    graph = {
        "11": {"class_type": "ImageSource", "inputs": {}},
        "3": {
            "class_type": "MiniMaxH3ImageConditioning",
            "inputs": {"images.image_1": ["11", 1]},
        },
    }

    assert codes(graph) == ["output_index_out_of_range"]


def test_primitive_input_rejects_link() -> None:
    graph = {
        "11": {"class_type": "ImageSource", "inputs": {}},
        "3": {
            "class_type": "TypeTarget",
            "inputs": {"number": ["11", 0], "image": ["11", 0]},
        },
    }

    assert codes(graph) == ["type_mismatch"]


def test_single_link_input_rejects_list_of_links() -> None:
    graph = {
        "11": {"class_type": "ImageSource", "inputs": {}},
        "3": {
            "class_type": "TypeTarget",
            "inputs": {"number": 1, "image": [["11", 0]]},
        },
    }

    assert codes(graph) == ["type_mismatch"]


def test_autogrow_rejects_slot_that_does_not_match_prefix() -> None:
    graph = {
        "11": {"class_type": "ImageSource", "inputs": {}},
        "3": {
            "class_type": "MiniMaxH3ImageConditioning",
            "inputs": {"images.reference_1": ["11", 0]},
        },
    }

    assert codes(graph) == ["unknown_input"]


def test_autogrow_accepts_correct_named_slots() -> None:
    graph = {
        "11": {"class_type": "ImageSource", "inputs": {}},
        "12": {"class_type": "ImageSource", "inputs": {}},
        "3": {
            "class_type": "MiniMaxH3ImageConditioning",
            "inputs": {
                "images.image_1": ["11", 0],
                "images.image_2": ["12", 0],
            },
        },
    }

    assert validate(graph) == []


def test_autogrow_base_name_also_requires_named_slot() -> None:
    graph = {
        "11": {"class_type": "ImageSource", "inputs": {}},
        "3": {
            "class_type": "MiniMaxH3ImageConditioning",
            "inputs": {"images": ["11", 0]},
        },
    }

    assert codes(graph) == ["dynamic_input_requires_named_slots"]


def test_cli_reports_shipped_bug_and_returns_one(tmp_path, capsys) -> None:
    graph = {
        "11": {"class_type": "LoadImage", "inputs": {"image": "a.png"}},
        "3": {
            "class_type": "MiniMaxH3ImageConditioning",
            "inputs": {"images": [["11", 0]]},
        },
    }
    graph_path = tmp_path / "graph.json"
    graph_path.write_text(json.dumps(graph), encoding="utf-8")

    exit_code = graph_validator.main(["--graph", str(graph_path)])

    assert exit_code == 1
    output = capsys.readouterr().out
    assert "3 MiniMaxH3ImageConditioning dynamic_input_requires_named_slots:" in output


def test_cli_accepts_valid_graph_and_returns_zero(tmp_path, capsys) -> None:
    graph = {
        "11": {"class_type": "LoadImage", "inputs": {"image": "a.png"}},
        "3": {
            "class_type": "MiniMaxH3ImageConditioning",
            "inputs": {"images.image_1": ["11", 0]},
        },
    }
    graph_path = tmp_path / "graph.json"
    graph_path.write_text(json.dumps(graph), encoding="utf-8")

    assert graph_validator.main(["--graph", str(graph_path)]) == 0
    assert capsys.readouterr().out == ""


def test_cli_returns_two_for_io_problem(tmp_path, capsys) -> None:
    assert graph_validator.main(["--graph", str(tmp_path / "missing.json")]) == 2
    assert "error:" in capsys.readouterr().err


def test_autogrow_accepts_explicit_declaration_names() -> None:
    object_info = dict(OBJECT_INFO)
    object_info["NamedReferences"] = {
        "input": {
            "required": {
                "references": [
                    "COMFY_AUTOGROW_V3",
                    {"names": ["image1", "image2"]},
                ]
            }
        },
        "output": ["CONDITIONING"],
    }
    graph = {
        "11": {"class_type": "ImageSource", "inputs": {}},
        "3": {
            "class_type": "NamedReferences",
            "inputs": {"references.image2": ["11", 0]},
        },
    }

    assert ComfyGraphValidator().validate_graph(graph, object_info) == []


def test_official_templates_have_no_false_positives() -> None:
    package = "comfyui_workflow_templates_json"
    if importlib.util.find_spec(package) is None:
        pytest.skip(f"{package} is not installed")

    try:
        with urllib.request.urlopen(graph_validator.OBJECT_INFO_URL, timeout=2) as response:
            object_info = json.loads(response.read().decode("utf-8"))
    except OSError as exc:
        pytest.skip(f"live ComfyUI object_info is unavailable: {exc}")

    from importlib import resources
    from scripts.comfy_ui_to_api import convert

    template_root = resources.files(package).joinpath("templates")
    index_path = template_root.joinpath("index.json")
    if not index_path.is_file():
        index_path = resources.files(package).joinpath("index.json")
        template_root = resources.files(package)
    categories = json.loads(index_path.read_text(encoding="utf-8"))
    template_names = [
        template["name"] for category in categories for template in category["templates"]
    ][:10]
    assert len(template_names) == 10

    failures = {}
    for name in template_names:
        workflow = json.loads(template_root.joinpath(name + ".json").read_text(encoding="utf-8"))
        graph = convert(workflow, object_info)
        issues = ComfyGraphValidator().validate_graph(graph, object_info)
        # A template can use custom nodes absent from the running ComfyUI; that
        # is an environment difference, not a validator false positive.
        issues = [issue for issue in issues if issue.code != "unknown_class_type"]
        if issues:
            failures[name] = [f"{issue.code}: {issue.message}" for issue in issues]
    assert not failures
