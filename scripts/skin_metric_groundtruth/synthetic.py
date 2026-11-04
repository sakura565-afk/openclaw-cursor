"""Synthetic plates with constructive labels and signed truth components.

The four core cases are the acceptance oracle: no texture, discrete pores,
isotropic noise, and pores plus noise.  Fine-pore diagnostics deliberately
retain the injector's normal fine scale; they are not relabelled to flatter the
classifier when its blur-survival statistic disagrees with their construction.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

import numpy as np
from PIL import Image

from scripts.pore_injector.field import build_pore_field

EXPECTED_CLASSES = {"plastic", "pores", "digital_sand"}
DEFAULT_SIZE = 192
DEFAULT_SEED = 4102
FWHM_IN_SIGMAS = 2.354820045


@dataclass(frozen=True)
class SyntheticCase:
    case_id: str
    family: str
    expected_class: str
    image: np.ndarray
    parameters: dict
    pore_delta: np.ndarray
    noise_delta: np.ndarray
    core: bool = False


def _pore_component(
    shape: tuple[int, int],
    *,
    seed: int,
    density: float,
    sigma: float,
    strength: float,
    rim: float,
    two_octave: bool,
    cluster_strength: float,
) -> tuple[np.ndarray, dict]:
    field, raw_stats = build_pore_field(
        shape,
        seed=seed,
        pit_density=density,
        pore_sigma=sigma,
        rim=rim,
        two_octave=two_octave,
        cluster_strength=cluster_strength,
    )
    pixels = shape[0] * shape[1]
    stats = dict(raw_stats)
    stats["actual_pit_density_per_10k"] = float(stats["pit_count"]) * 10_000.0 / pixels
    stats["requested_pit_density_per_10k"] = float(density)
    delta = np.asarray(field, dtype=np.float64) * float(strength)
    parameters = {
        "pore_seed": int(seed),
        "pore_strength_8bit": float(strength),
        "pore_sigma_px": float(sigma),
        "pore_fwhm_diameter_px": float(sigma * FWHM_IN_SIGMAS),
        "pore_rim": float(rim),
        "two_octave": bool(two_octave),
        "cluster_strength": float(cluster_strength),
        "pore_field": stats,
    }
    return delta, parameters


def _make_case(
    spec: dict,
    *,
    shape: tuple[int, int],
) -> SyntheticCase:
    base = float(spec["base"])
    pore_delta = np.zeros(shape, dtype=np.float64)
    noise_delta = np.zeros(shape, dtype=np.float64)
    parameters: dict = {
        "base_level": base,
        "noise_seed": None,
        "noise_sigma_requested_8bit": 0.0,
        "noise_sample_std_8bit": 0.0,
    }
    if spec.get("pore"):
        pore_delta, pore_parameters = _pore_component(shape, **spec["pore"])
        parameters.update(pore_parameters)
    if spec.get("noise_sigma", 0.0) > 0.0:
        noise_seed = int(spec["noise_seed"])
        rng = np.random.default_rng(noise_seed)
        noise_delta = rng.normal(0.0, float(spec["noise_sigma"]), shape)
        parameters.update(
            {
                "noise_seed": noise_seed,
                "noise_sigma_requested_8bit": float(spec["noise_sigma"]),
                "noise_sample_std_8bit": float(np.std(noise_delta)),
                "noise_distribution": "independent isotropic Gaussian per pixel",
            }
        )
    continuous = np.clip(base + pore_delta + noise_delta, 0.0, 255.0)
    image = np.rint(continuous).astype(np.uint8)
    return SyntheticCase(
        case_id=str(spec["id"]),
        family=str(spec["family"]),
        expected_class=str(spec["expected"]),
        image=image,
        parameters=parameters,
        pore_delta=pore_delta,
        noise_delta=noise_delta,
        core=bool(spec.get("core", False)),
    )


def _case_specs(seed: int) -> list[dict]:
    specs: list[dict] = []
    for index, base in enumerate((128, 96, 160, 192)):
        specs.append(
            {
                "id": "plastic_core" if index == 0 else f"plastic_level_{base}",
                "family": "plastic",
                "expected": "plastic",
                "base": base,
                "core": index == 0,
            }
        )

    # Larger, sparse pits are the constructive positive control for the existing
    # 3-12 px band plus sigma=1.2 blur.  These are not presented as natural skin.
    for index, strength in enumerate((80.0, 85.0, 90.0, 100.0)):
        specs.append(
            {
                "id": "pores_core" if index == 0 else f"pores_coarse_{index}",
                "family": "pores",
                "expected": "pores",
                "base": 128,
                "core": index == 0,
                "pore": {
                    "seed": seed + 17 + index * 12,
                    "density": 80.0,
                    "sigma": 3.5,
                    "strength": strength,
                    "rim": 0.0,
                    "two_octave": False,
                    "cluster_strength": 0.0,
                },
            }
        )

    for index, sigma in enumerate((12.0, 16.0, 20.0, 24.0)):
        specs.append(
            {
                "id": "noise_core" if index == 0 else f"noise_sigma_{int(sigma)}",
                "family": "noise",
                "expected": "digital_sand",
                "base": 128,
                "core": index == 0,
                "noise_seed": seed + 101 + index,
                "noise_sigma": sigma,
            }
        )

    for index, noise_sigma in enumerate((12.0, 16.0, 20.0, 24.0)):
        specs.append(
            {
                "id": "mixed_core" if index == 0 else f"mixed_{index}",
                "family": "mixed",
                "expected": "digital_sand",
                "base": 128,
                "core": index == 0,
                "pore": {
                    "seed": seed + 17 + index * 12,
                    "density": 80.0,
                    "sigma": 3.5,
                    "strength": 80.0 + index * 5.0,
                    "rim": 0.0,
                    "two_octave": False,
                    "cluster_strength": 0.0,
                },
                "noise_seed": seed + 201 + index,
                "noise_sigma": noise_sigma,
            }
        )

    # Independent failure probe: the pore injector's usual fine-pore regime.
    # Its known label remains pores even when the existing classifier rejects it.
    fine_settings = (
        (24.0, 260.0),
        (16.0, 260.0),
        (32.0, 260.0),
        (32.0, 120.0),
    )
    for index, (strength, density) in enumerate(fine_settings):
        specs.append(
            {
                "id": f"fine_pores_diagnostic_{index}",
                "family": "fine_pores",
                "expected": "pores",
                "base": 128,
                "pore": {
                    "seed": seed + 17 + index * 12,
                    "density": density,
                    "sigma": 1.1,
                    "strength": strength,
                    "rim": 0.45,
                    "two_octave": True,
                    "cluster_strength": 0.55,
                },
            }
        )
    return specs


def build_cases(
    *,
    size: int = DEFAULT_SIZE,
    seed: int = DEFAULT_SEED,
) -> list[SyntheticCase]:
    if size < 64:
        raise ValueError("size must be at least 64 pixels")
    shape = (int(size), int(size))
    cases = [_make_case(spec, shape=shape) for spec in _case_specs(int(seed))]
    identifiers = [case.case_id for case in cases]
    if len(identifiers) != len(set(identifiers)):
        raise RuntimeError("duplicate synthetic case id")
    return cases


def generate_dataset(
    out_dir: str | Path,
    *,
    size: int = DEFAULT_SIZE,
    seed: int = DEFAULT_SEED,
) -> list[dict]:
    """Write PNGs, signed NPZ components, and a relative-path JSON manifest."""
    root = Path(out_dir)
    image_dir = root / "images"
    component_dir = root / "components"
    image_dir.mkdir(parents=True, exist_ok=True)
    component_dir.mkdir(parents=True, exist_ok=True)
    entries: list[dict] = []
    for case in build_cases(size=size, seed=seed):
        image_relative = Path("images") / f"{case.case_id}.png"
        component_relative = Path("components") / f"{case.case_id}.npz"
        Image.fromarray(case.image).save(root / image_relative)
        np.savez_compressed(
            root / component_relative,
            pore_delta=case.pore_delta.astype(np.float32),
            noise_delta=case.noise_delta.astype(np.float32),
        )
        entries.append(
            {
                "case_id": case.case_id,
                "family": case.family,
                "expected_class": case.expected_class,
                "core": case.core,
                "image": image_relative.as_posix(),
                "components": component_relative.as_posix(),
                "parameters": case.parameters,
            }
        )
    manifest = {
        "tool": "scripts/skin_metric_groundtruth",
        "size": [int(size), int(size)],
        "seed": int(seed),
        "constructive_truth": {
            "plastic": "flat plate; exactly zero added pore and noise components",
            "pores": "sparse pore-injector pits; no added noise",
            "noise": "isotropic Gaussian noise; no pits",
            "mixed": "pits plus noise; expected digital_sand because the noise is present",
            "fine_pores": "normal fine pore-injector pits; diagnostic, not a core acceptance case",
        },
        "cases": entries,
    }
    (root / "synthetic_manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return entries
