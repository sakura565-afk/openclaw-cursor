#!/usr/bin/env python3
"""Validate ComfyUI API prompt graphs against /object_info schemas."""

from __future__ import annotations

import argparse
import json
import sys
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Any

PRIMITIVE_TYPES = frozenset({"INT", "FLOAT", "STRING", "BOOLEAN", "COMBO"})
DYNAMIC_INPUT_TYPES = frozenset(
    {"COMFY_AUTOGROW_V3", "COMFY_DYNAMICCOMBO_V3", "COMFY_DYNAMICGROUP_V3"}
)
ANY_TYPE = "*"
MATCH_TYPE = "COMFY_MATCHTYPE_V3"

BUNDLED_OBJECT_INFO: dict[str, Any] = {
    "LoadImage": {
        "input": {"required": {"image": ["STRING", {"default": ""}]}},
        "output": ["IMAGE", "MASK"],
        "name": "LoadImage",
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
            "required": {
                "ckpt_name": [
                    ["v1-5-pruned-emaonly.safetensors"],
                    {"tooltip": "checkpoint"},
                ],
            },
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
    "MiniMaxH3ImageConditioning": {
        "input": {
            "required": {
                "images": [
                    "COMFY_AUTOGROW_V3",
                    {
                        "template": {
                            "input": {
                                "required": {
                                    "image": ["IMAGE", {}],
                                },
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
}

CACHE_PATH = Path(__file__).resolve().parent / ".comfy_object_info_cache.json"
DEFAULT_OBJECT_INFO_URL = "http://127.0.0.1:8188/object_info"


@dataclass(frozen=True)
class Issue:
    node_id: str
    class_type: str
    code: str
    message: str
    severity: str = "error"


def is_link(value: Any) -> bool:
    if not isinstance(value, list) or len(value) != 2:
        return False
    if not isinstance(value[0], str):
        return False
    return isinstance(value[1], int) and not isinstance(value[1], bool)


def is_link_list(value: Any) -> bool:
    return isinstance(value, list) and len(value) > 0 and all(is_link(item) for item in value)


def type_name(spec: Any) -> str:
    if not isinstance(spec, (list, tuple)) or not spec:
        return "?"
    head = spec[0]
    if isinstance(head, str):
        return head
    if isinstance(head, list):
        return "COMBO"
    return str(head)


def spec_options(spec: Any) -> dict[str, Any]:
    if not isinstance(spec, (list, tuple)) or len(spec) < 2:
        return {}
    opts = spec[1]
    return opts if isinstance(opts, dict) else {}


def expects_link(spec: Any) -> bool:
    name = type_name(spec)
    if name in DYNAMIC_INPUT_TYPES:
        return False
    if name in PRIMITIVE_TYPES:
        return False
    if name == MATCH_TYPE:
        return True
    if name == ANY_TYPE:
        return True
    return True


def types_compatible(received: str, expected: str, *, strict: bool = False) -> bool:
    if received == expected:
        return True
    if received == ANY_TYPE or expected == ANY_TYPE:
        return True
    if received == MATCH_TYPE or expected == MATCH_TYPE:
        return True
    if not isinstance(received, str) or not isinstance(expected, str):
        return False
    received_types = {part.strip() for part in received.split(",") if part.strip()}
    expected_types = {part.strip() for part in expected.split(",") if part.strip()}
    if ANY_TYPE in received_types or ANY_TYPE in expected_types:
        return True
    if strict:
        return received_types.issubset(expected_types)
    return bool(received_types.intersection(expected_types))


def _node_sections(node_def: dict[str, Any]) -> dict[str, dict[str, Any]]:
    block = node_def.get("input", {})
    if not isinstance(block, dict):
        return {}
    sections: dict[str, dict[str, Any]] = {}
    for section in ("required", "optional", "hidden"):
        data = block.get(section, {})
        if isinstance(data, dict):
            sections[section] = data
    return sections


def _iter_declared_inputs(node_def: dict[str, Any]) -> list[tuple[str, Any, str]]:
    found: list[tuple[str, Any, str]] = []
    for section, fields in _node_sections(node_def).items():
        for name, spec in fields.items():
            found.append((name, spec, section))
    return found


def autogrow_slot_names(spec: Any) -> list[str]:
    opts = spec_options(spec)
    template = opts.get("template", {})
    if not isinstance(template, dict):
        return []
    if "names" in template and isinstance(template["names"], list):
        return [str(name) for name in template["names"]]
    prefix = template.get("prefix")
    if isinstance(prefix, str):
        max_count = int(template.get("max", 10))
        return [f"{prefix}{index}" for index in range(max_count)]
    return []


def autogrow_template_input_spec(spec: Any) -> Any | None:
    opts = spec_options(spec)
    template = opts.get("template", {})
    if not isinstance(template, dict):
        return None
    inner = template.get("input", {})
    if not isinstance(inner, dict):
        return None
    for bucket in ("required", "optional"):
        bucket_data = inner.get(bucket, {})
        if isinstance(bucket_data, dict) and bucket_data:
            return next(iter(bucket_data.values()))
    return None


def dynamic_combo_option_inputs(spec: Any, selected: str | None) -> dict[str, Any]:
    if selected is None:
        return {}
    opts = spec_options(spec)
    options = opts.get("options", [])
    if not isinstance(options, list):
        return {}
    for option in options:
        if not isinstance(option, dict):
            continue
        if str(option.get("key")) != selected:
            continue
        inputs = option.get("inputs", {})
        if isinstance(inputs, dict):
            merged: dict[str, Any] = {}
            for section in ("required", "optional"):
                section_data = inputs.get(section, {})
                if isinstance(section_data, dict):
                    merged.update(section_data)
            return merged
    return {}


def allowed_input_keys(node_def: dict[str, Any], node_inputs: dict[str, Any]) -> set[str]:
    allowed = set()
    for name, spec, _section in _iter_declared_inputs(node_def):
        allowed.add(name)
        if type_name(spec) == "COMFY_AUTOGROW_V3":
            for slot in autogrow_slot_names(spec):
                allowed.add(f"{name}.{slot}")
        elif type_name(spec) == "COMFY_DYNAMICCOMBO_V3":
            selected = node_inputs.get(name)
            if isinstance(selected, str):
                for sub_name in dynamic_combo_option_inputs(spec, selected):
                    allowed.add(f"{name}.{sub_name}")
            opts = spec_options(spec)
            for option in opts.get("options", []):
                if not isinstance(option, dict):
                    continue
                inputs = option.get("inputs", {})
                if not isinstance(inputs, dict):
                    continue
                for section in ("required", "optional"):
                    section_data = inputs.get(section, {})
                    if isinstance(section_data, dict):
                        for sub_name in section_data:
                            allowed.add(f"{name}.{sub_name}")
    return allowed


def resolve_input_spec(node_def: dict[str, Any], node_inputs: dict[str, Any], key: str) -> Any | None:
    for section in _node_sections(node_def).values():
        if key in section:
            return section[key]
    if "." not in key:
        return None
    parent, child = key.split(".", 1)
    for _name, spec, _section in _iter_declared_inputs(node_def):
        if _name != parent:
            continue
        kind = type_name(spec)
        if kind == "COMFY_AUTOGROW_V3":
            if child in autogrow_slot_names(spec):
                return autogrow_template_input_spec(spec)
        elif kind == "COMFY_DYNAMICCOMBO_V3":
            nested = dynamic_combo_option_inputs(spec, node_inputs.get(parent))
            if child in nested:
                return nested[child]
    return None


def input_is_required(node_def: dict[str, Any], key: str) -> bool:
    required = _node_sections(node_def).get("required", {})
    if key in required:
        return True
    if "." in key:
        parent, child = key.split(".", 1)
        parent_spec = required.get(parent) or _node_sections(node_def).get("optional", {}).get(parent)
        if parent_spec and type_name(parent_spec) == "COMFY_AUTOGROW_V3":
            template = spec_options(parent_spec).get("template", {})
            min_count = int(template.get("min", 0)) if isinstance(template, dict) else 0
            slots = autogrow_slot_names(parent_spec)
            if child in slots[:min_count]:
                return True
    return False


def missing_required_keys(
    node_def: dict[str, Any],
    node_inputs: dict[str, Any],
) -> list[str]:
    missing: list[str] = []
    required = _node_sections(node_def).get("required", {})
    for name, spec in required.items():
        kind = type_name(spec)
        if kind in DYNAMIC_INPUT_TYPES:
            if kind == "COMFY_AUTOGROW_V3":
                template = spec_options(spec).get("template", {})
                min_count = int(template.get("min", 0)) if isinstance(template, dict) else 0
                slots = autogrow_slot_names(spec)
                present = [
                    slot
                    for slot in slots[:max(min_count, len(slots))]
                    if f"{name}.{slot}" in node_inputs
                ]
                if min_count > 0 and len(present) < min_count:
                    missing.append(name)
            elif name not in node_inputs:
                missing.append(name)
            continue
        if name not in node_inputs:
            missing.append(name)
    return missing


class ComfyGraphValidator:
    def validate_graph(self, graph: dict[str, Any], object_info: dict[str, Any]) -> list[Issue]:
        issues: list[Issue] = []
        if not isinstance(graph, dict):
            return [
                Issue(
                    node_id="-",
                    class_type="-",
                    code="invalid_graph",
                    message="graph must be a JSON object",
                )
            ]

        for node_id, node in graph.items():
            if not isinstance(node, dict):
                issues.append(
                    Issue(
                        node_id=str(node_id),
                        class_type="?",
                        code="invalid_node",
                        message="node must be an object",
                    )
                )
                continue

            class_type = node.get("class_type")
            if not class_type:
                issues.append(
                    Issue(
                        node_id=str(node_id),
                        class_type="?",
                        code="missing_class_type",
                        message="missing class_type",
                    )
                )
                continue
            class_type = str(class_type)

            if class_type not in object_info:
                issues.append(
                    Issue(
                        node_id=str(node_id),
                        class_type=class_type,
                        code="unknown_class_type",
                        message=f"unknown class_type {class_type!r}",
                    )
                )
                continue

            node_def = object_info[class_type]
            if not isinstance(node_def, dict):
                issues.append(
                    Issue(
                        node_id=str(node_id),
                        class_type=class_type,
                        code="invalid_object_info",
                        message="invalid object_info entry",
                    )
                )
                continue

            inputs = node.get("inputs", {})
            if not isinstance(inputs, dict):
                issues.append(
                    Issue(
                        node_id=str(node_id),
                        class_type=class_type,
                        code="invalid_inputs",
                        message="inputs must be an object",
                    )
                )
                continue

            for req in missing_required_keys(node_def, inputs):
                issues.append(
                    Issue(
                        node_id=str(node_id),
                        class_type=class_type,
                        code="missing_required_input",
                        message=f"missing required input {req!r}",
                    )
                )

            allowed = allowed_input_keys(node_def, inputs)
            for key, value in inputs.items():
                spec = resolve_input_spec(node_def, inputs, key)
                if spec is None and key not in allowed:
                    issues.append(
                        Issue(
                            node_id=str(node_id),
                            class_type=class_type,
                            code="unknown_input",
                            message=f"input {key!r} is not declared on this node",
                        )
                    )
                    continue
                if spec is None:
                    continue

                declared_name = key.split(".", 1)[0] if "." in key else key
                for _name, dyn_spec, _section in _iter_declared_inputs(node_def):
                    if _name != declared_name:
                        continue
                    if type_name(dyn_spec) in DYNAMIC_INPUT_TYPES and key == declared_name:
                        if is_link_list(value):
                            issues.append(
                                Issue(
                                    node_id=str(node_id),
                                    class_type=class_type,
                                    code="dynamic_input_requires_named_slots",
                                    message=(
                                        f"dynamic input {declared_name!r} must use named slots "
                                        f"(e.g. {declared_name!r}.<slot>), not a list of links"
                                    ),
                                )
                            )
                        elif is_link(value):
                            issues.append(
                                Issue(
                                    node_id=str(node_id),
                                    class_type=class_type,
                                    code="dynamic_input_requires_named_slots",
                                    message=(
                                        f"dynamic input {declared_name!r} must use named slots, "
                                        "not a single link on the parent key"
                                    ),
                                )
                            )
                    break

                if is_link(value):
                    if not expects_link(spec):
                        issues.append(
                            Issue(
                                node_id=str(node_id),
                                class_type=class_type,
                                code="link_for_literal_input",
                                message=f"input {key!r} expects a literal, not a node link",
                            )
                        )
                        continue
                    ref_id, out_idx = value[0], int(value[1])
                    if ref_id not in graph:
                        issues.append(
                            Issue(
                                node_id=str(node_id),
                                class_type=class_type,
                                code="dangling_link",
                                message=f"link references missing node {ref_id!r}",
                            )
                        )
                        continue
                    ref = graph[ref_id]
                    if not isinstance(ref, dict):
                        continue
                    ref_type = str(ref.get("class_type", ""))
                    if ref_type not in object_info:
                        continue
                    outputs = object_info[ref_type].get("output", [])
                    if not isinstance(outputs, list):
                        issues.append(
                            Issue(
                                node_id=str(node_id),
                                class_type=class_type,
                                code="output_index_out_of_range",
                                message=f"node {ref_id!r} has no output list in object_info",
                            )
                        )
                        continue
                    if out_idx < 0 or out_idx >= len(outputs):
                        issues.append(
                            Issue(
                                node_id=str(node_id),
                                class_type=class_type,
                                code="output_index_out_of_range",
                                message=(
                                    f"output index {out_idx} out of range for node {ref_id!r} "
                                    f"({len(outputs)} outputs)"
                                ),
                            )
                        )
                        continue
                    received_type = str(outputs[out_idx])
                    expected_type = type_name(spec)
                    if not types_compatible(received_type, expected_type):
                        issues.append(
                            Issue(
                                node_id=str(node_id),
                                class_type=class_type,
                                code="type_mismatch",
                                message=(
                                    f"input {key!r} expects {expected_type}, "
                                    f"but node {ref_id!r} output {out_idx} is {received_type}"
                                ),
                            )
                        )
                elif expects_link(spec) and type_name(spec) not in DYNAMIC_INPUT_TYPES:
                    issues.append(
                        Issue(
                            node_id=str(node_id),
                            class_type=class_type,
                            code="literal_for_link_input",
                            message=f"input {key!r} expects a node link, not a literal",
                        )
                    )

        return issues


def load_json(path: Path) -> Any:
    with path.open("r", encoding="utf-8") as handle:
        return json.load(handle)


def fetch_object_info(url: str, cache_path: Path = CACHE_PATH) -> dict[str, Any]:
    payload: dict[str, Any] | None = None
    try:
        import requests  # type: ignore

        response = requests.get(url, timeout=120)
        response.raise_for_status()
        payload = response.json()
    except ImportError:
        with urllib.request.urlopen(url, timeout=120) as response:
            payload = json.loads(response.read().decode("utf-8"))
    except (urllib.error.URLError, OSError, json.JSONDecodeError, Exception) as exc:
        if cache_path.is_file():
            return load_json(cache_path)
        raise RuntimeError(f"failed to fetch object_info from {url}: {exc}") from exc

    if not isinstance(payload, dict):
        raise RuntimeError("object_info response is not a JSON object")
    cache_path.write_text(json.dumps(payload), encoding="utf-8")
    return payload


def format_issue(issue: Issue) -> str:
    return f"{issue.node_id} {issue.class_type} {issue.code}: {issue.message}"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Validate a ComfyUI API prompt graph.")
    parser.add_argument("--graph", required=True, type=Path, help="Path to API prompt JSON")
    parser.add_argument(
        "--object-info",
        nargs="?",
        const=DEFAULT_OBJECT_INFO_URL,
        default=None,
        help=(
            "Fetch /object_info from this URL (default "
            f"{DEFAULT_OBJECT_INFO_URL}) and cache it; omit to use bundled fixture"
        ),
    )
    args = parser.parse_args(argv)

    try:
        graph = load_json(args.graph)
    except (OSError, json.JSONDecodeError) as exc:
        print(f"failed to read graph: {exc}", file=sys.stderr)
        return 2

    try:
        if args.object_info is None:
            object_info = BUNDLED_OBJECT_INFO
        else:
            object_info = fetch_object_info(args.object_info)
    except (OSError, json.JSONDecodeError, RuntimeError) as exc:
        print(f"failed to load object_info: {exc}", file=sys.stderr)
        return 2

    issues = ComfyGraphValidator().validate_graph(graph, object_info)
    errors = [issue for issue in issues if issue.severity == "error"]
    for issue in issues:
        print(format_issue(issue))
    return 1 if errors else 0


if __name__ == "__main__":
    raise SystemExit(main())