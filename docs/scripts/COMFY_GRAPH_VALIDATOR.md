# ComfyUI workflow graph validator

Hand-built ComfyUI `/prompt` graphs are easy to ship with silent wiring bugs: the HTTP API
accepts JSON that the UI would never emit, and render output can look plausible while a
reference or control input is never connected.

`scripts/comfy_graph_validate.py` checks an API graph against node schemas from ComfyUI
`GET /object_info` (or a small bundled fixture when ComfyUI is offline).

## What is checked

For each node in the graph:

| Check | Code | Meaning |
| --- | --- | --- |
| Unknown node class | `unknown_class_type` | `class_type` is missing from `object_info` |
| Missing required input | `missing_required_input` | A required widget/socket was not provided |
| Unknown input key | `unknown_input` | An `inputs` key the node does not declare |
| Dangling link | `dangling_link` | `["node_id", index]` points at a missing node |
| Bad output index | `output_index_out_of_range` | Link index is outside the source node's `output` list |
| Literal where a link is required | `literal_for_link_input` | e.g. `clip: "x"` instead of `["3", 1]` |
| Link where a literal is required | `link_for_literal_input` | e.g. `image: ["1", 0]` on `LoadImage` |
| Linked type mismatch | `type_mismatch` | Source output type does not match the input socket |
| Dynamic input batch mistake | `dynamic_input_requires_named_slots` | AUTOGROW / dynamic combo given as a list of links |

Severity is currently `error` for all of the above. The CLI exits `0` when there are no
errors, `1` when errors exist, and `2` on usage or I/O failure.

## AUTOGROW named-slot rules

ComfyUI v3 dynamic inputs (`COMFY_AUTOGROW_V3`, `COMFY_DYNAMICCOMBO_V3`) expect **dotted
keys**, not a parent key whose value is a list of links.

**Broken (shipped bug pattern)** — reference images passed as a batch on the parent key;
ComfyUI does not map this to autogrow slots, so the model ignores the reference:

```json
"images": [["11", 0], ["12", 0]]
```

**Correct** — one key per slot; slot names come from the node's `object_info` template
(`prefix` + index, or an explicit `names` list):

```json
"images.image_1": ["11", 0],
"images.image_2": ["12", 0]
```

For `COMFY_DYNAMICCOMBO_V3`, the root key holds the selected option string; nested inputs
use `option.subfield` (for example `mode.strength`).

## Run against a live ComfyUI

With ComfyUI 0.45.x listening on the default port:

```bash
python scripts/comfy_graph_validate.py --graph workflows/my_prompt.json --object-info
```

`--object-info` without a value fetches `http://127.0.0.1:8188/object_info`, writes a cache
next to the script (`.comfy_object_info_cache.json`), and validates against that snapshot.
Pass a custom URL if your server is elsewhere:

```bash
python scripts/comfy_graph_validate.py --graph graph.json \
  --object-info http://192.168.1.10:8188/object_info
```

Omit `--object-info` to validate against the bundled minimal schema (useful in CI with no GPU).

Each issue is printed as one line:

```text
20 MiniMaxH3ImageConditioning dynamic_input_requires_named_slots: dynamic input 'images' must use named slots ...
```

## Tests

```bash
uv run --with pytest --with pillow --with requests --quiet python -m pytest \
  tests/test_comfy_graph_validate.py -q --override-ini="addopts="
```

When `comfyui_workflow_templates_json` is installed, the suite also smoke-checks ten official
templates for `unknown_class_type` false positives (skipped otherwise).