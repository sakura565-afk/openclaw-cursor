"""Static validation for ComfyUI API-format prompt graphs."""
from __future__ import annotations

import argparse
from dataclasses import dataclass
import json
from pathlib import Path
import re
import sys
from typing import Any
import urllib.request


DYNAMIC_INPUT_TYPES = {"COMFY_AUTOGROW_V3", "COMBO", "COMFY_DYNAMICCOMBO_V3"}
PRIMITIVE_TYPES = {"INT", "FLOAT", "STRING", "BOOLEAN"}
OBJECT_INFO_URL = "http://127.0.0.1:8188/object_info"
OBJECT_INFO_CACHE = Path.home() / ".cache" / "comfy_graph_validate" / "object_info.json"

# Offline fallback for the reference-image regression this validator guards.
# Use --object-info for complete, version-accurate schemas.
BUNDLED_OBJECT_INFO = {
    "LoadImage": {
        "input": {"required": {"image": ["STRING", {}]}},
        "output": ["IMAGE", "MASK"],
    },
    "MiniMaxH3ImageConditioning": {
        "input": {
            "required": {
                "images": ["COMFY_AUTOGROW_V3", {"prefix": "image_", "min": 1}],
            }
        },
        "output": ["CONDITIONING"],
    },
}


@dataclass(frozen=True)
class Issue:
    node_id: str
    class_type: str
    code: str
    message: str
    severity: str


class ComfyGraphValidator:
    """Validate a graph against ComfyUI's ``/object_info`` node schemas."""

    def validate_graph(self, graph: dict, object_info: dict) -> list[Issue]:
        issues: list[Issue] = []
        graph_by_id = {str(key): value for key, value in graph.items()}
        for node_id, node in graph.items():
            class_type = node.get("class_type", "")
            if class_type not in object_info:
                issues.append(Issue(
                    str(node_id), class_type, "unknown_class_type",
                    f"node class '{class_type}' is not present in object_info",
                    "error",
                ))
                continue
            schema = object_info[class_type]
            required = schema.get("input", {}).get("required", {})
            inputs = node.get("inputs", {})
            for input_name in required:
                if input_name not in inputs and not any(
                    key.startswith(input_name + ".") for key in inputs
                ):
                    issues.append(Issue(
                        str(node_id), class_type, "missing_required_input",
                        f"required input '{input_name}' is missing",
                        "error",
                    ))
            declarations = dict(required)
            declarations.update(schema.get("input", {}).get("optional", {}))
            for input_name, value in inputs.items():
                declaration = declarations.get(input_name)
                if declaration is None and "." in input_name:
                    base_declaration = declarations.get(input_name.split(".", 1)[0])
                    if (
                        base_declaration
                        and self._is_dynamic(base_declaration)
                        and self._valid_dynamic_slot(
                            base_declaration, input_name.split(".", 1)[1]
                        )
                    ):
                        declaration = base_declaration
                if declaration is None:
                    issues.append(Issue(
                        str(node_id), class_type, "unknown_input",
                        f"input '{input_name}' is not declared by {class_type}",
                        "error",
                    ))
                    continue
                input_type = self._declared_type(declaration)
                if input_type in PRIMITIVE_TYPES and self._is_link(value):
                    issues.append(Issue(
                        str(node_id), class_type, "type_mismatch",
                        f"input '{input_name}' expects {input_type}, not a link",
                        "error",
                    ))
                if self._is_dynamic(declaration) and (
                    "." not in input_name or self._is_link_list(value)
                ):
                    issues.append(Issue(
                        str(node_id), class_type,
                        "dynamic_input_requires_named_slots",
                        f"dynamic input '{input_name}' requires named slots, not a list of links",
                        "error",
                    ))
                elif (
                    input_type not in PRIMITIVE_TYPES
                    and not self._is_dynamic(declaration)
                    and self._is_link_list(value)
                ):
                    issues.append(Issue(
                        str(node_id), class_type, "type_mismatch",
                        f"input '{input_name}' expects one {input_type} link, not a list of links",
                        "error",
                    ))
                links = value if self._is_link_list(value) else [value]
                for link in links:
                    if not self._is_link(link):
                        continue
                    source_id = str(link[0])
                    if source_id not in graph_by_id:
                        issues.append(Issue(
                            str(node_id), class_type, "dangling_link",
                            f"input '{input_name}' links to missing node '{source_id}'",
                            "error",
                        ))
                        continue
                    source = graph_by_id[source_id]
                    source_class = source.get("class_type", "")
                    if source_class not in object_info:
                        continue
                    outputs = object_info[source_class].get("output", [])
                    output_index = link[1]
                    if output_index < 0 or output_index >= len(outputs):
                        issues.append(Issue(
                            str(node_id), class_type, "output_index_out_of_range",
                            f"input '{input_name}' uses output {output_index} of "
                            f"{source_id} ({source_class}), which declares {len(outputs)} outputs",
                            "error",
                        ))
        return issues

    @staticmethod
    def _is_dynamic(declaration: Any) -> bool:
        raw_type = declaration[0] if isinstance(declaration, list) and declaration else declaration
        return isinstance(raw_type, str) and raw_type in DYNAMIC_INPUT_TYPES

    @staticmethod
    def _valid_dynamic_slot(declaration: Any, slot: str) -> bool:
        options = declaration[1] if len(declaration) > 1 else {}
        if not isinstance(options, dict):
            return bool(slot)
        names = options.get("names")
        if isinstance(names, list) and slot in names:
            return True
        prefix = options.get("prefix")
        if not isinstance(prefix, str):
            template = options.get("template")
            if isinstance(template, str):
                prefix = re.split(r"[\{*]", template, maxsplit=1)[0]
        if isinstance(prefix, str):
            match = re.fullmatch(re.escape(prefix) + r"(\d+)", slot)
            return match is not None and int(match.group(1)) >= 1
        return not names and bool(slot)

    @staticmethod
    def _declared_type(declaration: Any) -> str:
        raw_type = declaration[0] if isinstance(declaration, list) and declaration else declaration
        return "COMBO" if isinstance(raw_type, list) else str(raw_type)

    @staticmethod
    def _is_link(value: Any) -> bool:
        return (
            isinstance(value, list)
            and len(value) == 2
            and isinstance(value[0], (str, int))
            and isinstance(value[1], int)
            and not isinstance(value[1], bool)
        )

    @staticmethod
    def _is_link_list(value: Any) -> bool:
        return isinstance(value, list) and bool(value) and all(
            isinstance(item, list) and len(item) == 2 for item in value
        )


def load_live_object_info(
    url: str = OBJECT_INFO_URL,
    cache_path: Path = OBJECT_INFO_CACHE,
) -> dict:
    """Fetch and cache node schemas, falling back to the last good response."""
    try:
        with urllib.request.urlopen(url, timeout=30) as response:
            object_info = json.loads(response.read().decode("utf-8"))
        if not isinstance(object_info, dict):
            raise ValueError("/object_info did not return a JSON object")
        cache_path.parent.mkdir(parents=True, exist_ok=True)
        cache_path.write_text(
            json.dumps(object_info, ensure_ascii=False), encoding="utf-8"
        )
        return object_info
    except OSError:
        if cache_path.exists():
            cached = json.loads(cache_path.read_text(encoding="utf-8"))
            if isinstance(cached, dict):
                return cached
        raise


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--graph", required=True, help="API-format prompt graph JSON")
    parser.add_argument(
        "--object-info",
        nargs="?",
        const=OBJECT_INFO_URL,
        help=f"fetch /object_info (default: {OBJECT_INFO_URL}); cached for offline reuse",
    )
    args = parser.parse_args(argv)
    try:
        graph = json.loads(Path(args.graph).read_text(encoding="utf-8"))
        if not isinstance(graph, dict):
            raise ValueError("graph JSON must be an object keyed by node id")
        object_info = (
            load_live_object_info(args.object_info)
            if args.object_info
            else BUNDLED_OBJECT_INFO
        )
    except (OSError, ValueError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2

    issues = ComfyGraphValidator().validate_graph(graph, object_info)
    for issue in issues:
        print(
            f"{issue.node_id} {issue.class_type} {issue.code}: {issue.message}"
        )
    return 1 if any(issue.severity == "error" for issue in issues) else 0


if __name__ == "__main__":
    raise SystemExit(main())
