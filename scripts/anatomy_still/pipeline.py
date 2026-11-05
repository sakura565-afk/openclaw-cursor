"""Iterative Qwen edit -> pose mask -> local refine -> numerical QC."""
from __future__ import annotations

import os
import shutil
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Callable

from PIL import Image

from .metrics import validate_anatomy
from .pose_mask import build_mask_from_image, save_mask
from .recipes import get_recipe


def _read_rgb(path: str | Path):
    import numpy as np

    with Image.open(path) as image:
        return np.asarray(image.convert("RGB"))


@contextmanager
def _environment(values: dict[str, str]):
    previous = {key: os.environ.get(key) for key in values}
    os.environ.update(values)
    try:
        yield
    finally:
        for key, value in previous.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value


class AnatomyStillPipeline:
    """Run one scene recipe; all GPU calls are injectable and absent in dry-run."""

    def __init__(
        self,
        scene: str,
        *,
        recipes_path: str | Path | None = None,
        edit_runner: Callable[..., Any] | None = None,
        refine_runner: Callable[..., Any] | None = None,
        mask_builder: Callable[..., Any] | None = None,
        validator: Callable[..., Any] | None = None,
    ) -> None:
        self.scene = scene
        self.recipe = get_recipe(scene, recipes_path)
        self.edit_runner = edit_runner or self._run_edit
        self.refine_runner = refine_runner or self._run_refine
        self.mask_builder = mask_builder or self._build_mask
        self.validator = validator or self._validate

    def _plan(self) -> list[dict[str, Any]]:
        edit = self.recipe["edit"]
        passes: list[dict[str, Any]] = [
            {
                "kind": "edit",
                "backend": edit.get("backend", "qwen_edit"),
                "denoise": float(edit["denoise"]),
                "steps": int(edit["steps"]),
                "seed": int(edit["seed"]),
                "loras": edit["loras"],
            }
        ]
        for index, refine in enumerate(self.recipe["refine"]["passes"], start=1):
            passes.append(
                {
                    "kind": "masked_refine",
                    "iteration": index,
                    "denoise": float(refine["denoise"]),
                    "steps": int(refine["steps"]),
                    "seed": int(edit["seed"]) + int(refine["seed_offset"]),
                    "anatomy_lora_strength": float(refine["lora_strength"]),
                    "backend": refine.get("backend", self.recipe["refine"].get("backend", "qwen_masked_fix")),
                    "mask": self.recipe["mask"],
                }
            )
        return passes

    def _run_h3_edit(
        self,
        *,
        source: Path,
        tag: str,
        steps: int,
        seed: int,
    ) -> Path:
        """Render the still with H3 instead of an image editor.

        Qwen-Image-Edit censors the masked region: inside the pose mask the
        texture contrast collapsed from 5.23 in the source to 1.15 after the
        edit - a flat grey fill at the same brightness. H3 with a minimal LoRA
        stack is the path that does not censor.
        """
        from scripts import local_paths
        from scripts.anatomy_still import h3_still

        h3 = self.recipe["h3"]
        render = h3.get("render", {})
        out = Path(local_paths.SCRATCH) / f"{tag}_h3.png"
        return h3_still.render_still(
            source=source,
            prompt=h3["prompt"],
            loras=h3["loras"],
            out_png=out,
            width=int(render.get("width", self.recipe["size"][0])),
            height=int(render.get("height", self.recipe["size"][1])),
            length=int(render.get("length", 33)),
            steps=int(render.get("steps", steps)),
            seed=int(render.get("seed", seed)),
            sampler=str(render.get("sampler", "euler")),
            scheduler=str(render.get("scheduler", "simple")),
            negative=str(h3.get("negative", "")),
        )

    def _run_edit(
        self,
        *,
        source: Path,
        tag: str,
        denoise: float,
        steps: int,
        seed: int,
        backend: str = "qwen_edit",
        **_: Any,
    ) -> Path:
        if backend == "h3_still":
            return self._run_h3_edit(source=source, tag=tag, steps=steps, seed=seed)

        from scripts import local_paths
        from scripts import qwen_edit_run

        input_name = f"anatomy_{tag}_source{source.suffix.lower()}"
        shutil.copy2(source, local_paths.PROD_IN / input_name)
        loras = self.recipe["edit"]["loras"]
        primary = loras[0]
        secondary = loras[1] if len(loras) > 1 else None
        env = {
            "QWEN_NSFW_LORA": primary["name"],
            "QWEN_NSFW_LORA_STRENGTH": str(primary["strength"]),
        }
        if secondary:
            env.update(
                QWEN_LORA2=secondary["name"],
                QWEN_LORA2_STRENGTH=str(secondary["strength"]),
            )
        with _environment(env):
            code = qwen_edit_run.run(
                tag,
                input_name,
                self.recipe["edit"]["prompt"],
                seed,
                denoise,
                steps,
                tuple(self.recipe["size"]),
            )
        if code != 0:
            raise RuntimeError(f"Qwen edit failed with exit code {code}")
        output = local_paths.SCRATCH / f"qe_{tag}.png"
        if not output.exists():
            raise RuntimeError(f"Qwen edit returned no still: {output}")
        return output

    def _build_mask(self, image: Path, recipe: dict[str, Any]) -> Path:
        from scripts import local_paths

        rgb = _read_rgb(image)
        mask, pose = build_mask_from_image(rgb, recipe["mask"])
        output = local_paths.SCRATCH / f"anatomy_{image.stem}_mask.png"
        save_mask(output, mask)
        pose_output = output.with_suffix(".json")
        import json

        pose_output.write_text(json.dumps(pose.to_dict(), indent=2), encoding="utf-8")
        return output

    def _validate(self, image: Path, mask: Path):
        return validate_anatomy(_read_rgb(image), _read_rgb(mask)[:, :, 0])

    def _run_refine(
        self,
        *,
        source: Path,
        mask: Path,
        tag: str,
        denoise: float,
        steps: int,
        seed: int,
        anatomy_lora_strength: float,
        backend: str = "qwen_masked_fix",
        **_: Any,
    ) -> Path:
        if backend == "qwen21_masked_refine":
            return self._run_q21_refine(
                source=source,
                mask=mask,
                tag=tag,
                denoise=denoise,
                steps=steps,
                seed=seed,
                anatomy_lora_strength=anatomy_lora_strength,
            )
        if backend != "qwen_masked_fix":
            raise ValueError(f"unsupported masked-refine backend: {backend}")

        from scripts import local_paths
        from scripts import qwen_masked_fix

        src_name = f"anatomy_{tag}_src.png"
        mask_name = f"anatomy_{tag}_mask.png"
        shutil.copy2(source, local_paths.PROD_IN / src_name)
        shutil.copy2(mask, local_paths.PROD_IN / mask_name)
        loras = self.recipe["edit"]["loras"]
        primary = loras[0]
        anatomy = loras[-1]
        env = {
            "QWEN_NSFW_LORA": primary["name"],
            "QWEN_NSFW_LORA_STRENGTH": str(primary["strength"]),
            "QWEN_LORA2": anatomy["name"],
            "QWEN_LORA2_STRENGTH": str(anatomy_lora_strength),
        }
        with _environment(env):
            output = qwen_masked_fix.run(
                tag,
                src_name,
                mask_name,
                self.recipe["refine"]["prompt"],
                seed,
                denoise,
                steps,
                tuple(self.recipe["size"]),
                int(self.recipe["mask"].get("grow", 6)),
            )
        if output is None or not Path(output).exists():
            raise RuntimeError("masked Qwen refinement returned no still")
        return Path(output)

    def _run_q21_refine(
        self,
        *,
        source: Path,
        mask: Path,
        tag: str,
        denoise: float,
        steps: int,
        seed: int,
        anatomy_lora_strength: float,
    ) -> Path:
        from scripts import local_paths

        src_name = f"anatomy_{tag}_src.png"
        mask_name = f"anatomy_{tag}_mask.png"
        shutil.copy2(source, local_paths.SCRATCH / src_name)
        shutil.copy2(mask, local_paths.SCRATCH / mask_name)
        anatomy = self.recipe["edit"]["loras"][-1]
        env = {
            "Q21_LORA": anatomy["name"],
            "Q21_LORA_STRENGTH": str(anatomy_lora_strength),
        }
        with _environment(env):
            from scripts import qwen21_masked_refine as q21

            previous = (q21.POS, q21.LORA, q21.LORA_STR)
            q21.POS = self.recipe["refine"]["prompt"]
            q21.LORA = anatomy["name"]
            q21.LORA_STR = float(anatomy_lora_strength)
            try:
                output = q21.run(
                    tag,
                    src=src_name,
                    mask=mask_name,
                    denoise=denoise,
                    steps=steps,
                    seed=seed,
                    grow=int(self.recipe["mask"].get("grow", 6)),
                    size=tuple(self.recipe["size"]),
                )
            finally:
                q21.POS, q21.LORA, q21.LORA_STR = previous
        if output is None or not Path(output).exists():
            raise RuntimeError("Qwen-2.1 masked refinement returned no still")
        return Path(output)

    def run(
        self,
        source: str | Path,
        output_dir: str | Path,
        *,
        dry_run: bool = False,
    ) -> dict[str, Any]:
        source = Path(source)
        output_dir = Path(output_dir)
        plan = self._plan()
        if dry_run:
            return {
                "scene": self.scene,
                "status": "dry_run_unverified",
                "source": str(source),
                "passes": plan,
                "h3_prompt_suffix": self.recipe["h3"]["prompt_suffix"],
            }
        if not source.exists():
            raise FileNotFoundError(source)
        output_dir.mkdir(parents=True, exist_ok=True)
        current = source
        history: list[dict[str, Any]] = []
        last_metrics: Any = None
        for pass_spec in plan:
            kind = pass_spec["kind"]
            tag = f"anatomy_{self.scene}_{len(history):02d}"
            if kind == "edit":
                produced = self.edit_runner(source=source, tag=tag, **pass_spec)
                history.append({"pass": pass_spec})
            else:
                produced = self.refine_runner(
                    source=current,
                    mask=mask,
                    tag=tag,
                    **{k: v for k, v in pass_spec.items() if k not in {"kind", "iteration", "mask"}},
                )
                history.append({"pass": pass_spec})
            produced = Path(produced)
            current = produced
            mask = Path(self.mask_builder(current, self.recipe))
            last_metrics = self.validator(current, mask)
            history[-1]["still"] = str(current)
            history[-1]["mask"] = str(mask)
            history[-1]["metrics"] = last_metrics.to_dict()
            if last_metrics.passed:
                break
        if last_metrics is None:
            raise RuntimeError("pipeline executed no passes")
        final_path = output_dir / current.name
        if current.resolve() != final_path.resolve():
            shutil.copy2(current, final_path)
        final_mask = output_dir / mask.name
        if mask.resolve() != final_mask.resolve():
            shutil.copy2(mask, final_mask)
        return {
            "scene": self.scene,
            "status": "passed" if last_metrics.passed else "failed",
            "still": str(final_path),
            "mask": str(final_mask),
            "visibility": last_metrics.visibility,
            "metrics": last_metrics.to_dict(),
            "passes": history,
            "h3_prompt_suffix": self.recipe["h3"]["prompt_suffix"],
        }
