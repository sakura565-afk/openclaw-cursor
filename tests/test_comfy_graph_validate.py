"""Tests for scripts.comfy_graph_validate."""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.comfy_graph_validate import ComfyGraphValidator, Issue  # noqa: E402

FIXTURE_OBJECT_INFO = {
    "LoadImage": {
        "input": {"required": {"image": ["STRING", {"default": ""}]}},
        "output": ["IMAGE", "MASK"],
    },
    "MiniMaxH3ImageConditioning": {
        "input": {
            "required": {
                "images": [
                    "COMFY_AUTOGROW_V3",
                    {
                        "template": {
                            "input": {
                                "required": {"image": ["IMAGE", {}]},
                            },
                            "prefix": "image_",
                            "min": 1,
                            "max": 4,
                        },
                    },
                ],
                "prompt": ["STRING", {"multiline": True}],
            },
        },
        "output": ["CONDITIONING"],
    },
    "CLIPTextEncode": {
        "input": {
            "required": {
                "text": ["STRING", {"multiline": True}],
                "clip": ["CLIP", {}],
            },
        },
        "output": ["CONDITIONING"],
    },
    "CheckpointLoaderSimple": {
        "input": {
            "required": {"ckpt_name": [["model.safetensors"], {}]},
        },
        "output": ["MODEL", "CLIP", "VAE"],
    },
    "KSampler": {
        "input": {
            "required": {
                "model": ["MODEL", {}],
                "positive": ["CONDITIONING", {}],
                "negative": ["CONDITIONING", {}],
                "latent_image": ["LATENT", {}],
                "seed": ["INT", {"default": 0}],
                "steps": ["INT", {"default": 20}],
                "cfg": ["FLOAT", {"default": 8.0}],
                "sampler_name": [["euler"], {}],
                "scheduler": [["normal"], {}],
                "denoise": ["FLOAT", {"default": 1.0}],
            },
        },
        "output": ["LATENT"],
    },
    "EmptyLatentImage": {
        "input": {
            "required": {
                "width": ["INT", {"default": 512}],
                "height": ["INT", {"default": 512}],
                "batch_size": ["INT", {"default": 1}],
            },
        },
        "output": ["LATENT"],
    },
    "ModeSwitch": {
        "input": {
            "required": {
                "mode": [
                    "COMFY_DYNAMICCOMBO_V3",
                    {
                        "options": [
                            {
                                "key": "on",
                                "inputs": {
                                    "required": {"strength": ["FLOAT", {"default": 1.0}]},
                                },
                            },
                        ],
                    },
                ],
            },
        },
        "output": ["FLOAT"],
    },
}


def validate(graph: dict) -> list[Issue]:
    return ComfyGraphValidator().validate_graph(graph, FIXTURE_OBJECT_INFO)


def codes(issues: list[Issue]) -> set[str]:
    return {issue.code for issue in issues}


def base_graph() -> dict:
    return {
        "10": {"class_type": "LoadImage", "inputs": {"image": "ref.png"}},
        "11": {"class_type": "LoadImage", "inputs": {"image": "ref2.png"}},
    }


class TestDynamicAutogrow:
    def test_shipped_bug_list_of_links(self):
        graph = {
            **base_graph(),
            "20": {
                "class_type": "MiniMaxH3ImageConditioning",
                "inputs": {
                    "prompt": "hero",
                    "images": [["11", 0], ["12", 0]],
                },
            },
        }
        issues = validate(graph)
        assert "dynamic_input_requires_named_slots" in codes(issues)

    def test_named_slot_is_valid(self):
        graph = {
            **base_graph(),
            "20": {
                "class_type": "MiniMaxH3ImageConditioning",
                "inputs": {
                    "prompt": "hero",
                    "images.image_1": ["11", 0],
                },
            },
        }
        issues = validate(graph)
        assert not issues


class TestBasics:
    def test_missing_required_input(self):
        graph = {
            "1": {
                "class_type": "CLIPTextEncode",
                "inputs": {"text": "hello"},
            }
        }
        issues = validate(graph)
        assert "missing_required_input" in codes(issues)

    def test_unknown_class_type(self):
        graph = {"1": {"class_type": "NotARealNode", "inputs": {}}}
        issues = validate(graph)
        assert "unknown_class_type" in codes(issues)

    def test_unknown_input(self):
        graph = {
            "1": {
                "class_type": "LoadImage",
                "inputs": {"image": "a.png", "extra": 1},
            }
        }
        issues = validate(graph)
        assert "unknown_input" in codes(issues)

    def test_dangling_link(self):
        graph = {
            "1": {
                "class_type": "CLIPTextEncode",
                "inputs": {"text": "x", "clip": ["99", 0]},
            }
        }
        issues = validate(graph)
        assert "dangling_link" in codes(issues)

    def test_output_index_out_of_range(self):
        graph = {
            "1": {"class_type": "CheckpointLoaderSimple", "inputs": {"ckpt_name": "model.safetensors"}},
            "2": {
                "class_type": "CLIPTextEncode",
                "inputs": {"text": "x", "clip": ["1", 9]},
            },
        }
        issues = validate(graph)
        assert "output_index_out_of_range" in codes(issues)

    def test_literal_for_link_input(self):
        graph = {
            "1": {
                "class_type": "CLIPTextEncode",
                "inputs": {"text": "x", "clip": "not-a-link"},
            }
        }
        issues = validate(graph)
        assert "literal_for_link_input" in codes(issues)

    def test_link_for_literal_input(self):
        graph = {
            "1": {"class_type": "LoadImage", "inputs": {"image": ["2", 0]}},
            "2": {"class_type": "EmptyLatentImage", "inputs": {"width": 512, "height": 512, "batch_size": 1}},
        }
        issues = validate(graph)
        assert "link_for_literal_input" in codes(issues)

    def test_type_mismatch_on_link(self):
        graph = {
            "1": {"class_type": "LoadImage", "inputs": {"image": "a.png"}},
            "2": {
                "class_type": "CLIPTextEncode",
                "inputs": {"text": "x", "clip": ["1", 0]},
            },
        }
        issues = validate(graph)
        assert "type_mismatch" in codes(issues)

    def test_valid_minimal_sampler_graph(self):
        graph = {
            "1": {"class_type": "CheckpointLoaderSimple", "inputs": {"ckpt_name": "model.safetensors"}},
            "2": {"class_type": "CLIPTextEncode", "inputs": {"text": "pos", "clip": ["1", 1]}},
            "3": {"class_type": "CLIPTextEncode", "inputs": {"text": "neg", "clip": ["1", 1]}},
            "4": {"class_type": "EmptyLatentImage", "inputs": {"width": 512, "height": 512, "batch_size": 1}},
            "5": {
                "class_type": "KSampler",
                "inputs": {
                    "model": ["1", 0],
                    "positive": ["2", 0],
                    "negative": ["3", 0],
                    "latent_image": ["4", 0],
                    "seed": 1,
                    "steps": 20,
                    "cfg": 7.5,
                    "sampler_name": "euler",
                    "scheduler": "normal",
                    "denoise": 1.0,
                },
            },
        }
        issues = validate(graph)
        assert not issues


class TestDynamicCombo:
    def test_dynamic_combo_named_subkeys(self):
        graph = {"1": {"class_type": "ModeSwitch", "inputs": {"mode": "on", "mode.strength": 0.5}}}
        issues = validate(graph)
        assert not issues

    def test_dynamic_combo_list_of_links_rejected(self):
        graph = {"1": {"class_type": "ModeSwitch", "inputs": {"mode": [["2", 0]]}}}
        issues = validate(graph)
        assert "dynamic_input_requires_named_slots" in codes(issues)


class TestCli:
    def test_cli_exit_codes(self, tmp_path: Path):
        graph_path = tmp_path / "graph.json"
        graph_path.write_text(
            json.dumps(
                {
                    "1": {
                        "class_type": "MiniMaxH3ImageConditioning",
                        "inputs": {"prompt": "x", "images": [["11", 0]]},
                    }
                }
            ),
            encoding="utf-8",
        )
        from scripts import comfy_graph_validate as module

        assert module.main(["--graph", str(graph_path)]) == 1


class TestOfficialTemplates:
    def test_official_templates_have_no_false_positive_errors(self):
        if importlib.util.find_spec("comfyui_workflow_templates_json") is None:
            pytest.skip("comfyui_workflow_templates_json is not installed")

        import comfyui_workflow_templates_json as templates_pkg  # type: ignore

        from scripts.comfy_graph_validate import DEFAULT_OBJECT_INFO_URL, fetch_object_info
        from scripts.comfy_ui_to_api import convert

        try:
            object_info = fetch_object_info(DEFAULT_OBJECT_INFO_URL)
        except RuntimeError:
            pytest.skip("ComfyUI /object_info is not reachable")

        template_dir = Path(templates_pkg.__file__).resolve().parent / "templates"
        if not template_dir.is_dir():
            pytest.skip("template directory missing in comfyui_workflow_templates_json")

        ui_templates = sorted(template_dir.glob("api_*.json"))[:10]
        if not ui_templates:
            ui_templates = sorted(template_dir.glob("*.json"))[:10]
        if not ui_templates:
            pytest.skip("no templates found in comfyui_workflow_templates_json")

        validator = ComfyGraphValidator()
        for path in ui_templates:
            workflow = json.loads(path.read_text(encoding="utf-8"))
            if not isinstance(workflow, dict) or "nodes" not in workflow:
                continue
            graph = convert(workflow, object_info)
            issues = validator.validate_graph(graph, object_info)
            errors = [issue for issue in issues if issue.severity == "error"]
            assert not errors, f"{path.name}: {errors}"