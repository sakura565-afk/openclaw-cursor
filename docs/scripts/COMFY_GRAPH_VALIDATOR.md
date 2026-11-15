# ComfyUI API graph validator

`scripts/comfy_graph_validate.py` statically checks an API-format prompt graph against ComfyUI node schemas. It does not execute the graph or contact ComfyUI during validation.

## Run

Validate with the small offline schema fixture:

```bash
python scripts/comfy_graph_validate.py --graph graph.json
```

For real workflows, use the schemas from the target ComfyUI instance. This is important when custom nodes or a different ComfyUI version is installed:

```bash
# Fetches http://127.0.0.1:8188/object_info
python scripts/comfy_graph_validate.py --graph graph.json --object-info

# Or select another server
python scripts/comfy_graph_validate.py --graph graph.json \
  --object-info http://127.0.0.1:8188/object_info
```

A successful fetch is cached at `~/.cache/comfy_graph_validate/object_info.json`. If a later fetch fails, that cache is used. Without `--object-info`, the bundled fixture only covers `LoadImage` and the MiniMax H3 reference-image regression; use live schemas to avoid `unknown_class_type` for every other node.

Output contains one issue per line:

```text
3 MiniMaxH3ImageConditioning dynamic_input_requires_named_slots: dynamic input 'images' requires named slots
```

Exit status is `0` for no errors, `1` when validation finds errors, and `2` for command-line, JSON, file, or schema-fetch failures.

## Checks

| Code | Check |
| --- | --- |
| `unknown_class_type` | The node's `class_type` is absent from `object_info`. |
| `missing_required_input` | A required input, or at least one named slot for a required dynamic input, is absent. |
| `unknown_input` | An input key is not declared, including a dynamic slot that does not match its declared names or prefix. |
| `dangling_link` | A link names a source node that is absent from the graph. |
| `output_index_out_of_range` | A link's output index is negative or outside the source node's declared `output` list. |
| `type_mismatch` | An `INT`, `FLOAT`, `STRING`, or `BOOLEAN` input receives a link, or a single-link input receives a list of links. |
| `dynamic_input_requires_named_slots` | An autogrow/dynamic input is supplied under its base name instead of named slots, including as a plain list of links. |

The Python API returns dataclass instances with `node_id`, `class_type`, `code`, `message`, and `severity` fields:

```python
from scripts.comfy_graph_validate import ComfyGraphValidator

issues = ComfyGraphValidator().validate_graph(graph, object_info)
```

## Dynamic inputs and named slots

Inputs declared as `COMFY_AUTOGROW_V3`, `COMFY_DYNAMICCOMBO_V3`, or the dynamic string type `COMBO` use separate keys for each value. Do not put a list under the declaration's base name.

For an AUTOGROW declaration with prefix `image_`, use `image_1`, `image_2`, and so on:

```json
{
  "11": {"class_type": "LoadImage", "inputs": {"image": "first.png"}},
  "12": {"class_type": "LoadImage", "inputs": {"image": "second.png"}},
  "3": {
    "class_type": "MiniMaxH3ImageConditioning",
    "inputs": {
      "images.image_1": ["11", 0],
      "images.image_2": ["12", 0]
    }
  }
}
```

The full input key is `<declared input>.<slot>`. A declaration's `template` can provide the same numeric prefix rule. If it provides `names`, such as `['image1', 'image2']`, those exact slot names are accepted instead: `references.image1`, `references.image2`.

This is invalid, even though every individual link is valid:

```json
"images": [["11", 0], ["12", 0]]
```

### Why the batched form is silently ignored

An AUTOGROW input is not an `IMAGE` batch input. Its Python input parser reads the named slots and builds the node's internal reference collection. The base key `images` with a nested list does not populate those slots. The graph can therefore look structurally plausible and run without an obvious type error while the reference images never reach the model. Named slots make every reference explicit and allow this validator to check each link independently.
