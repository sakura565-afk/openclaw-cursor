"""Convert a ComfyUI UI workflow (workflow JSON) into an API prompt graph.

ComfyUI's example workflows ship in the editor format: nodes carry an ordered
``widgets_values`` list and links are a separate array. The HTTP API wants
``{node_id: {class_type, inputs}}`` with every widget resolved by name.

The converter also applies a small overlay so a template can be reused with
different inputs (prompt text, resolution, source video) without hand-editing
the JSON.

Usage:
  python scripts/comfy_ui_to_api.py <workflow.json> --out prompt.json \
      --set "9.prompt=my prompt" --set "9.width=736"
"""
from __future__ import annotations

import argparse
import json
import re
import urllib.request
from pathlib import Path
from typing import Any

OBJECT_INFO_URL = "http://127.0.0.1:8188/object_info"


def _widget_names(node: dict) -> list[str]:
    """Input names that come from widgets, in widget order."""
    return [i["name"] for i in node.get("inputs", []) if isinstance(i, dict) and "widget" in i]


def load_schema(url: str = OBJECT_INFO_URL) -> dict:
    """Live node schema, so widget names come from ComfyUI itself."""
    with urllib.request.urlopen(url, timeout=120) as response:
        return json.loads(response.read().decode())


PRIMITIVE_TYPES = {"INT", "FLOAT", "STRING", "BOOLEAN"}


def _input_kind(value) -> str:
    """Normalise an object_info input spec to a type name.

    A combo input is stored as a list of options, a primitive as its name.
    """
    kind = value[0] if isinstance(value, list) and value else value
    if isinstance(kind, list):
        return "COMBO"
    return str(kind)


def _schema_widget_names(schema: dict, class_type: str, linked: set[str]) -> list[str]:
    """Ordered widget-only input names for a node.

    The editor stores widget values as an ordered list without names. The node
    definition supplies the names: every required and optional input whose
    type is a primitive (int/float/string/bool) or a combo. Inputs that carry
    data (IMAGE, MODEL, CLIP_VISION, ...) have no widget.
    """
    spec = schema.get(class_type)
    if not spec:
        return []
    ins = spec.get("input", {})
    names: list[str] = []
    for section in ("required", "optional"):
        for name, value in ins.get(section, {}).items():
            if _input_kind(value) not in PRIMITIVE_TYPES and _input_kind(value) != "COMBO":
                continue
            names.append(name)
    return names


def convert(workflow: dict, schema: dict | None = None) -> dict:
    nodes = {str(n["id"]): n for n in workflow.get("nodes", [])}
    links = workflow.get("links", [])

    # link id -> (origin node id, origin slot)
    link_map: dict[int, tuple[str, int]] = {}
    for l in links:
        link_id, origin_id, origin_slot = l[0], l[1], l[2]
        link_map[link_id] = (str(origin_id), origin_slot)

    prompt: dict[str, Any] = {}
    for nid, node in nodes.items():
        class_type = node.get("type")
        if not class_type or class_type.startswith("Note"):
            continue
        inputs: dict[str, Any] = {}

        linked_names: set[str] = set()
        for spec in node.get("inputs", []):
            if isinstance(spec, dict) and spec.get("link") is not None:
                linked_names.add(spec["name"])

        # widget values, matched by name from the live node schema
        wnames = _widget_names(node)
        wvals = node.get("widgets_values")
        if isinstance(wvals, list):
            if not wnames and schema is not None:
                wnames = _schema_widget_names(schema, class_type, linked_names)
            for i, val in enumerate(wvals):
                key = wnames[i] if i < len(wnames) else f"widget_{i}"
                inputs[key] = val
        elif isinstance(wvals, dict):
            inputs.update(wvals)

        # linked inputs override widgets
        for spec in node.get("inputs", []):
            if not isinstance(spec, dict):
                continue
            link_id = spec.get("link")
            if link_id is None:
                continue
            if link_id not in link_map:
                continue
            origin_id, origin_slot = link_map[link_id]
            inputs[spec["name"]] = [origin_id, origin_slot]

        prompt[nid] = {"class_type": class_type, "inputs": inputs}
    return prompt


def apply_overrides(prompt: dict, overrides: list[str]) -> dict:
    for item in overrides:
        if "=" not in item:
            raise SystemExit(f"--set ожидает node.key=value, получено: {item}")
        path, raw = item.split("=", 1)
        node_id, _, key = path.partition(".")
        if node_id not in prompt:
            raise SystemExit(f"нет узла {node_id} в графе")
        value: Any = raw
        if re.fullmatch(r"-?\d+", raw):
            value = int(raw)
        elif re.fullmatch(r"-?\d+\.\d+", raw):
            value = float(raw)
        elif raw.lower() in ("true", "false"):
            value = raw.lower() == "true"
        prompt[node_id]["inputs"][key] = value
    return prompt


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("workflow")
    ap.add_argument("--out", default="")
    ap.add_argument("--set", action="append", default=[],
                    help="node.key=value (repeatable)")
    ap.add_argument("--no-schema", action="store_true",
                    help="do not fetch the live node schema")
    a = ap.parse_args()

    workflow = json.load(open(a.workflow, encoding="utf-8"))
    schema = None if a.no_schema else load_schema()
    prompt = convert(workflow, schema)
    if a.set:
        prompt = apply_overrides(prompt, a.set)
    text = json.dumps(prompt, ensure_ascii=False, indent=1)
    if a.out:
        Path(a.out).write_text(text, encoding="utf-8")
        print(f"узлов: {len(prompt)} -> {a.out}")
    else:
        print(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
