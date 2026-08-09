# ComfyUI Impact Pack — Compatibility with ComfyUI v0.31.1

> Research-only root-cause analysis. This document does **not** modify upstream
> Impact Pack code and does **not** propose any upstream PR. The companion
> hardening patch lives at [`patches/impact_pack_v0311.diff`](../patches/impact_pack_v0311.diff)
> and is intended to be applied locally to our vendored copy of the node pack.

- **Node pack:** [`ltdrdata/ComfyUI-Impact-Pack`](https://github.com/ltdrdata/ComfyUI-Impact-Pack)
- **Version analysed:** `8.28.3` (commit [`429d015`](https://github.com/ltdrdata/ComfyUI-Impact-Pack/commit/429d0159ad429e64d2b3916e6e7be9c22d025c3c), 2026-04-20)
- **ComfyUI upgrade:** internal `v0.29.2` → `v0.31.1` (the V3-schema / `io.ComfyNode` wave)
- **Affected file:** `modules/impact/wildcards.py`

---

## Section 1 — Problem statement

After upgrading ComfyUI from `v0.29.2` to `v0.31.1`, any workflow containing
Impact Pack detailer nodes fails during **pre-validation / prompt loading** with
one of two variants of the same `AttributeError`:

```
AttributeError: 'str' object has no attribute 'items'
```

or, depending on the code path that is reached first:

```
AttributeError: 'str' object has no attribute 'values'
```

**When it occurs.** The crash fires while ComfyUI is loading/validating the
prompt graph and Impact Pack is (re)building its wildcard dictionary — i.e.
*before* sampling starts. It is triggered as soon as the wildcard subsystem
walks the `wildcards/` and `custom_wildcards/` directories and encounters a
`.yaml`/`.yml` file whose top-level document does **not** parse into a Python
`dict` (mapping). Because wildcard loading is global and lazily re-triggered by
the detailer nodes, the failure surfaces on the first Impact node that ComfyUI
validates.

**Nodes affected (observed).**

- `FaceDetailer`
- `DetailerForEach` / `DetailerForEachPipe` (and their `...Test` variants)
- `SAMDetector` (`SAMDetectorCombined` / `SAMDetectorSegmented`)

`FaceDetailer` and `DetailerForEach` expose a `wildcard` prompt input that flows
into `wildcards.process_with_loras()` / `wildcards.process_wildcard_for_segs()`,
both of which depend on the wildcard dictionary produced by
`read_wildcard_dict()`. `SAMDetector` sits in the same detailer pipeline and is
commonly validated in the same graph, so it appears in the same failure batch
even though it does not itself read wildcard prompts. The unifying fact is that
**the exception is raised at wildcard-load time, not inside any single node's
`execute`**, which is why the error looks like a generic "pre-validation"
failure rather than a node-specific bug.

---

## Section 2 — Investigation log (upstream issues & PRs)

Searched `ltdrdata/ComfyUI-Impact-Pack` issues/PRs for the keywords requested:
`'str' object has no attribute values`, `'str' object has no attribute items`,
`0.31`, and `V3 API`.

1. **[Issue #1065 — "Impact Wildcard: AttributeError: 'str' object has no attribute 'items'"](https://github.com/ltdrdata/ComfyUI-Impact-Pack/issues/1065)**
   *Direct match.* The reporter's traceback ends in
   `modules/impact/wildcards.py … for k, v in yaml_data.items()` →
   `AttributeError: 'str' object has no attribute 'items'`. Maintainer/community
   conclusion: the crash was caused by a **malformed YAML wildcard file** — a
   `.yaml`/`.yml` document that PyYAML parses into a bare `str` (or a `list`)
   instead of a mapping. The suggested workaround is a `deep_validate.py`
   script that walks the wildcard tree and flags every file that does *not*
   `yaml.safe_load()` into a `dict`. **Status:** resolved as a data problem; no
   defensive guard was added to the loader, so the same input still crashes the
   node pack. This is the canonical upstream reference for our `.items()`
   variant.

2. **[Issue #1112 — "After ComfyUI converts nodes_differential_diffusion.py to V3 schema (#10056) cause BUG about .apply()"](https://github.com/ltdrdata/ComfyUI-Impact-Pack/issues/1112)**
   Documents the *other* half of the "Impact Pack breaks after a ComfyUI
   upgrade" story: ComfyUI PR `#10056` migrated `nodes_differential_diffusion.py`
   to the **V3 schema**, renaming `DifferentialDiffusion().apply()` to
   `.execute()`. Impact Pack's `do_detail()` (used by `FaceDetailer` /
   `DetailerForEach`) called the old method, producing
   `'DifferentialDiffusion' object has no attribute 'apply'`. **Status:** fixed
   upstream. Relevant because it confirms the V3 migration wave *did* break the
   same detailer nodes — just via a different symptom than ours.

3. **[Issue #1131 — "FaceDetailer Node Failed After Recent ComfyUI Update (…'DifferentialDiffusion' object has no attribute 'execute')"](https://github.com/ltdrdata/ComfyUI-Impact-Pack/issues/1131)** and
   **[Issue #1177 — "DifferentialDiffusion does not have attribute 'execute' - SOLVED"](https://github.com/ltdrdata/ComfyUI-Impact-Pack/issues/1177)**
   The mirror-image of #1112 (version skew in the other direction). The
   maintainer's resolution (issue #1177) is the key compatibility milestone:
   *"Addressed in commit `d74c1d0` (V8.28.3). `modules/impact/utils.py` exposes
   `apply_differential_diffusion(model)` which dispatches based on `hasattr`:
   `.execute` → V3 classmethod (ComfyUI ≥ 0.3.63); `.apply` → legacy instance
   method; neither → AttributeError with upgrade guidance."* The README also
   states **"V8.24: this compatibility patch requires ComfyUI version 0.3.63 or
   higher due to structural changes in DifferentialDiffusion."**

4. **[Issue #1076 — "Control Bridge inside subgraph causes workflow crash"](https://github.com/ltdrdata/ComfyUI-Impact-Pack/issues/1076)**
   Not our error, but useful context: the maintainer notes that many Impact
   nodes still use a **wildcard "*" type** for arbitrary connections and that
   *"type validation may still produce error messages"* until ComfyUI ships
   native dynamic types. This explains why Impact validation is fragile across
   ComfyUI upgrades in general.

**Conclusion of the search.** No upstream issue attributes the
`'str' object has no attribute 'values'/'items'` error to the V3 (`io.ComfyNode`)
migration. The only issue that reproduces *our exact* message (#1065) points
squarely at the YAML wildcard loader. The V3 migration produced a *separate*
family of `DifferentialDiffusion.apply/execute` errors (#1112/#1131/#1177) that
was already fixed in `8.28.3`.

---

## Section 3 — Code analysis

The current pack (`8.28.3`) still registers nodes with the **classic V1**
pattern (`INPUT_TYPES` classmethods + `NODE_CLASS_MAPPINGS`); a repo-wide search
for `io.ComfyNode`, `define_schema`, `comfy_api.latest`, and `ComfyExtension`
returns **no matches**. Therefore the crash is **not** a V1 → V3 migration bug
inside Impact Pack, and it is not caused by a changed *input type*. It is a
long-standing **input-hardening gap** in the YAML wildcard loader that ComfyUI
`v0.31.1` makes far easier to hit (stricter/earlier prompt validation plus the
V3-era re-trigger of wildcard loading during graph load).

The throwing code lives in `modules/impact/wildcards.py`. There are three
unguarded call sites that assume `yaml.load(...)` returned a `dict`:

- **`load_yaml_wildcard()`** — the `.items()` variant:

```388:388:modules/impact/wildcards.py
    for k, v in yaml_data.items():
```

  …and the `.values()` variant at the end of the same function, which is the
  source of the `'str' object has no attribute 'values'` message:

```422:422:modules/impact/wildcards.py
    return result if result else list(yaml_data.values())
```

- **`read_wildcard_dict()`** — the on-demand branch:

```499:500:modules/impact/wildcards.py
                        for k, v in yaml_data.items():
                            read_wildcard(k, v, on_demand)
```

  …and the immediate-load branch:

```510:511:modules/impact/wildcards.py
                    for k, v in yaml_data.items():
                        read_wildcard(k, v, on_demand)
```

Each site is reached right after a `yaml.load(f, Loader=yaml.FullLoader)` call.
The only prior guard is a falsy check (`if not yaml_data: return []`), which
catches `None`/empty but **not** a non-empty `str` or `list`. PyYAML returns a
bare `str` whenever a file's top-level document is scalar text — e.g. a plain
prompt list saved without a mapping key, a file starting with `--- ` followed by
free text, or a `.yaml` file that is really a TXT wildcard list. When that
happens, `str.items()` / `str.values()` do not exist and the `AttributeError`
propagates up through `read_wildcard_dict()` → `wildcard_load()` and aborts
prompt validation.

This precisely reproduces both error strings in the ticket: `.items()` (lines
388/499/510) yields *"'str' object has no attribute 'items'"* and `.values()`
(line 422) yields *"'str' object has no attribute 'values'"*, depending on which
path the offending file drives.

---

## Section 4 — Recommended fix (minimal patch)

**Scope:** 1 file, ~24 added lines, no behavioural change for valid data.

Harden every place that calls `.items()`/`.values()` on the YAML result so a
single malformed wildcard file is **skipped with a warning** instead of aborting
wildcard loading (and therefore prompt validation). Concretely, in
`modules/impact/wildcards.py`:

1. In `load_yaml_wildcard()`, immediately after the existing
   `if not yaml_data: return []`, add
   `if not isinstance(yaml_data, dict):` → log a warning and `return` the list
   (if it is a list) or `[]`. This also protects the trailing
   `list(yaml_data.values())` because `yaml_data` is now guaranteed to be a
   `dict` beyond that point.
2. In `read_wildcard_dict()` (on-demand branch), change `if yaml_data:` to
   `if isinstance(yaml_data, dict):` and add an `elif yaml_data:` warning.
3. In `read_wildcard_dict()` (immediate branch), add
   `if not isinstance(yaml_data, dict): … continue` before the `for k, v`
   loop.

The patch keeps the exact same output for well-formed mapping YAML, converts a
hard crash into a skipped-file warning for malformed input, and leaves TXT
wildcard handling untouched. It does not depend on any ComfyUI V1/V3 symbol, so
it is safe across the `v0.29.2` → `v0.31.1` upgrade and forward.

**Apply locally (do not commit into `custom_nodes/`):**

```bash
cd custom_nodes/ComfyUI-Impact-Pack
patch -p1 < ../../patches/impact_pack_v0311.diff
```

**Verification performed for this patch:**

- `git apply --check` parses the diff cleanly against the analysed commit.
- `patch -p1 --dry-run` and a real `patch -p1` both apply against a fresh clone.
- `python3 -m py_compile modules/impact/wildcards.py` succeeds post-patch.
- In our repo, `git apply --check patches/impact_pack_v0311.diff` fails only with
  `No such file or directory` (the target paths intentionally live in the node
  pack, not in this repo) — confirming the diff **header syntax is valid** while
  honouring the "do not vendor upstream code" constraint.

> Note: the deeper data fix is to correct the offending wildcard `.yaml`/`.yml`
> file(s) so they parse as mappings (see the `deep_validate.py` approach in
> issue #1065). The patch is defence-in-depth so that one bad file can never
> again take down prompt validation for `FaceDetailer` / `DetailerForEach` /
> `SAMDetector`.

---

## Section 5 — References

- Impact Pack issue #1065 — `'str' object has no attribute 'items'` (wildcards):
  <https://github.com/ltdrdata/ComfyUI-Impact-Pack/issues/1065>
- Impact Pack issue #1112 — V3 schema (`#10056`) breaks `DifferentialDiffusion.apply`:
  <https://github.com/ltdrdata/ComfyUI-Impact-Pack/issues/1112>
- Impact Pack issue #1131 — FaceDetailer fails after ComfyUI update (`.execute`):
  <https://github.com/ltdrdata/ComfyUI-Impact-Pack/issues/1131>
- Impact Pack issue #1177 — DifferentialDiffusion `.execute` **SOLVED** in V8.28.3 (`d74c1d0`):
  <https://github.com/ltdrdata/ComfyUI-Impact-Pack/issues/1177>
- Impact Pack issue #1076 — wildcard/dynamic-type validation caveats:
  <https://github.com/ltdrdata/ComfyUI-Impact-Pack/issues/1076>
- Impact Pack source — `modules/impact/wildcards.py` (commit `429d015`):
  <https://github.com/ltdrdata/ComfyUI-Impact-Pack/blob/429d0159ad429e64d2b3916e6e7be9c22d025c3c/modules/impact/wildcards.py>
- Impact Pack issue tracker (search entry point):
  <https://github.com/ltdrdata/ComfyUI-Impact-Pack/issues>
- ComfyUI V3 custom-node migration guide (`io.ComfyNode`, `io.Schema`, `execute`):
  <https://docs.comfy.org/custom-nodes/v3_migration>
- ComfyUI V3 example node (`comfy_api.latest`, `ComfyExtension`):
  <https://github.com/comfyanonymous/ComfyUI/blob/master/custom_nodes/example_node.py.example>
- ComfyUI custom-node development overview (DeepWiki):
  <https://deepwiki.com/Comfy-Org/ComfyUI/6.1-creating-custom-nodes>
- PyYAML loading semantics (`yaml.load` returns scalar `str` for bare documents):
  <https://pyyaml.org/wiki/PyYAMLDocumentation>
