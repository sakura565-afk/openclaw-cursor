#!/usr/bin/env python3
"""Auto-LoRA Stack Selector for ComfyUI (FLUX.2 Klein 9B + Pony v6 XL).

Standalone CLI utility that, given a text prompt, picks the optimal LoRA stack
and sampler settings for the Klein 9B / Pony v6 XL / Wan2.2 pipelines and emits a
JSON snippet that can be deep-merged into an existing ComfyUI workflow JSON.

This tool never talks to ComfyUI. It only reads presets, analyses a prompt, and
writes JSON config.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

# Optional pretty output. The tool degrades gracefully when rich is not
# installed. JSON on stdout is always emitted via stdlib json so it stays
# machine-readable/parseable regardless of rich.
try:
    from rich.console import Console

    _HAVE_RICH = True
except Exception:  # pragma: no cover - rich is optional
    _HAVE_RICH = False

DEFAULT_PRESETS_PATH = Path(__file__).resolve().parent.parent / "config" / "lora_presets.yaml"
DEFAULT_MODELS_ROOT = Path(r"E:\AI\models\comfyui")

# Keyword categories used for prompt analysis. Matching is whole-word and
# case-insensitive (multi-word phrases are matched as substrings).
KEYWORDS: dict[str, list[str]] = {
    "explicit_toy": [
        "dildo", "toy", "toys", "vibrator", "buttplug", "plug", "strapon", "strap-on",
    ],
    "penetration": [
        "penetration", "penetrate", "penetrating", "intercourse", "insertion",
        "cock", "cunt", "fuck", "fucking", "anal",
    ],
    "nsfw": [
        "nude", "naked", "nsfw", "topless", "explicit", "erotic", "pussy",
        "breast", "breasts", "nipple", "nipples",
    ],
    "beach": [
        "beach", "ocean", "wave", "waves", "shore", "sand", "sea", "coast", "water",
    ],
    "furniture": [
        "furniture", "sofa", "couch", "table", "chair", "desk", "product",
        "render", "3d render", "interior", "showroom",
    ],
    "editorial": [
        "portrait", "headshot", "editorial", "hegre", "fashion", "studio",
        "beauty shot", "face",
    ],
    "video": [
        "video", "animate", "animation", "motion", "wan2.2", "wan22", "clip",
    ],
    "faceswap": [
        "face swap", "faceswap", "face-swap", "swap face", "replace face",
        "bfs", "bfs head",
    ],
}

# Pose keywords -> canonical pose id / risk classification.
POSE_KEYWORDS: dict[str, list[str]] = {
    "squat": ["squat", "squatting", "deep squat", "crouch", "crouching"],
    "stand": ["stand", "standing"],
    "kneel": ["kneel", "kneeling", "kneeled"],
    "walk": ["walk", "walking", "shore walk"],
    "lying": ["lying", "lie", "laying", "lay down", "reclining"],
}

# Scoring weights per preset. score = sum(weight * distinct-matches-in-category).
PRESET_SCORING: dict[str, dict[str, int]] = {
    "wan22_video": {"video": 3},
    "face_swap": {"faceswap": 3},
    "nsfw_explicit": {"explicit_toy": 3, "penetration": 3, "nsfw": 1},
    "beach_safe": {"beach": 2, "nsfw": 1},
    "nsfw_soft": {"nsfw": 2},
    "editorial": {"editorial": 2},
    "furniture": {"furniture": 2},
    "penetration_fallback": {"penetration": 3},
}

# Tie-break priority (lower index wins when scores are equal).
PRESET_PRIORITY: list[str] = [
    "wan22_video",
    "face_swap",
    "nsfw_explicit",
    "beach_safe",
    "nsfw_soft",
    "editorial",
    "furniture",
    "penetration_fallback",
]

DEFAULT_PRESET = "editorial"


@dataclass
class Preset:
    """A resolved LoRA/sampler preset."""

    preset_id: str
    description: str
    checkpoint: str
    loras: list[dict[str, Any]]
    sampler: dict[str, Any]
    negative: str
    warnings: list[str] = field(default_factory=list)
    approved_poses: list[str] = field(default_factory=list)
    forbidden_poses: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "preset_id": self.preset_id,
            "description": self.description,
            "checkpoint": self.checkpoint,
            "loras": self.loras,
            "sampler": self.sampler,
            "negative": self.negative,
            "warnings": self.warnings,
            "approved_poses": self.approved_poses,
            "forbidden_poses": self.forbidden_poses,
        }


@dataclass
class AnalysisResult:
    """Result of analysing a prompt."""

    prompt: str
    matched: dict[str, list[str]]
    poses: dict[str, list[str]]
    scores: dict[str, float]
    ranked: list[tuple[str, float]]
    pose_warnings: list[str] = field(default_factory=list)

    @property
    def top_preset(self) -> str:
        return self.ranked[0][0] if self.ranked else DEFAULT_PRESET

    def to_dict(self) -> dict[str, Any]:
        return {
            "prompt": self.prompt,
            "matched": {k: v for k, v in self.matched.items() if v},
            "poses": {k: v for k, v in self.poses.items() if v},
            "scores": self.scores,
            "ranked": [{"preset": p, "confidence": round(c, 3)} for p, c in self.ranked],
            "pose_warnings": self.pose_warnings,
        }


@dataclass
class ValidationReport:
    """Result of validating that a preset's files exist on disk."""

    ok: bool
    missing: list[str]
    warnings: list[str]

    def to_dict(self) -> dict[str, Any]:
        return {"ok": self.ok, "missing": self.missing, "warnings": self.warnings}


def _match_keywords(prompt: str, keywords: list[str]) -> list[str]:
    """Return the distinct keywords found in *prompt* (whole-word, case-insensitive)."""
    found: list[str] = []
    for kw in keywords:
        if " " in kw or "." in kw or "-" in kw:
            if kw in prompt:
                found.append(kw)
        elif re.search(rf"\b{re.escape(kw)}\b", prompt):
            found.append(kw)
    return found


class LoRASelector:
    """Selects LoRA stacks + sampler settings from a text prompt."""

    def __init__(self, presets_path: Path, models_root: Path) -> None:
        self.presets_path = Path(presets_path)
        self.models_root = Path(models_root)
        data = self._load_yaml(self.presets_path)
        self._presets_raw: dict[str, Any] = data.get("presets", {}) or {}
        self._checkpoints: dict[str, Any] = data.get("checkpoints", {}) or {}
        if not self._presets_raw:
            raise ValueError(f"No presets found in {self.presets_path}")

    @staticmethod
    def _load_yaml(path: Path) -> dict[str, Any]:
        if not path.exists():
            raise FileNotFoundError(f"Presets file not found: {path}")
        with path.open("r", encoding="utf-8") as fh:
            return yaml.safe_load(fh) or {}

    def list_presets(self) -> list[str]:
        """Return preset ids ordered by tie-break priority, then any extras."""
        known = [p for p in PRESET_PRIORITY if p in self._presets_raw]
        extras = [p for p in self._presets_raw if p not in known]
        return known + extras

    def get_preset(self, preset_id: str) -> Preset:
        raw = self._presets_raw.get(preset_id)
        if raw is None:
            raise KeyError(f"Unknown preset: {preset_id!r}. Known: {', '.join(self.list_presets())}")
        return Preset(
            preset_id=preset_id,
            description=raw.get("description", ""),
            checkpoint=raw.get("checkpoint", ""),
            loras=[dict(lora) for lora in (raw.get("loras") or [])],
            sampler=dict(raw.get("sampler") or {}),
            negative=(raw.get("negative") or "").strip(),
            warnings=list(raw.get("warnings") or []),
            approved_poses=list(raw.get("approved_poses") or []),
            forbidden_poses=list(raw.get("forbidden_poses") or []),
        )

    def analyze(self, prompt: str) -> AnalysisResult:
        """Detect content + pose keywords and rank presets by confidence."""
        low = prompt.lower()
        matched = {cat: _match_keywords(low, kws) for cat, kws in KEYWORDS.items()}
        poses = {pose: _match_keywords(low, kws) for pose, kws in POSE_KEYWORDS.items()}

        raw_scores: dict[str, float] = {}
        for preset_id in self.list_presets():
            weights = PRESET_SCORING.get(preset_id, {})
            raw_scores[preset_id] = float(
                sum(weight * len(matched.get(cat, [])) for cat, weight in weights.items())
            )

        total = sum(raw_scores.values())
        if total <= 0:
            # Nothing matched: default to the safe SFW editorial preset.
            scores = {pid: (1.0 if pid == DEFAULT_PRESET else 0.0) for pid in raw_scores}
        else:
            scores = {pid: raw / total for pid, raw in raw_scores.items()}

        priority_index = {pid: i for i, pid in enumerate(PRESET_PRIORITY)}
        ranked = sorted(
            scores.items(),
            key=lambda kv: (-kv[1], priority_index.get(kv[0], len(priority_index))),
        )

        pose_warnings = self._pose_warnings(matched, poses)

        return AnalysisResult(
            prompt=prompt,
            matched=matched,
            poses=poses,
            scores={pid: round(s, 3) for pid, s in scores.items()},
            ranked=ranked,
            pose_warnings=pose_warnings,
        )

    @staticmethod
    def _pose_warnings(matched: dict[str, list[str]], poses: dict[str, list[str]]) -> list[str]:
        warnings: list[str] = []
        has_toy = bool(matched.get("explicit_toy"))
        near_water = bool(matched.get("beach"))
        if poses.get("squat"):
            if has_toy:
                warnings.append("FORBIDDEN: deep squat + toy on Klein → THREE ARMS artifact.")
            warnings.append("RISKY: squat poses on Klein cause foot/toe deformation.")
        if poses.get("walk") and near_water:
            warnings.append(
                "RISKY: walking_water (side/back) on Klein fuses arm with thigh — use shore_walk."
            )
        if poses.get("lying"):
            warnings.append(
                "RISKY: lying_sand on Klein gives seed-dependent asymmetric breasts — reroll seed."
            )
        return warnings

    def select(self, prompt: str, checkpoint: str = "auto") -> Preset:
        """Pick the best preset for *prompt*, honouring the checkpoint override."""
        analysis = self.analyze(prompt)
        best_id = analysis.top_preset
        checkpoint = (checkpoint or "auto").lower()
        has_penetration = bool(analysis.matched.get("penetration"))

        if checkpoint == "pony":
            best_id = "penetration_fallback"
        elif checkpoint == "klein":
            if best_id == "penetration_fallback":
                best_id = "nsfw_explicit"
        else:  # auto: Klein for editorial/furniture, Pony for real penetration.
            if has_penetration and best_id in {"nsfw_explicit", "nsfw_soft", "penetration_fallback"}:
                best_id = "penetration_fallback"

        if best_id not in self._presets_raw:
            best_id = DEFAULT_PRESET
        return self.get_preset(best_id)

    def _checkpoint_entry(self, checkpoint: str) -> dict[str, Any]:
        return dict(self._checkpoints.get(checkpoint) or {})

    def to_workflow_patch(self, preset: Preset) -> dict[str, Any]:
        """Return a dict deep-mergeable into a ComfyUI workflow JSON."""
        entry = self._checkpoint_entry(preset.checkpoint)
        model_key = entry.get("key", "checkpoint")
        ckpt_path = entry.get("path")
        model_name = Path(ckpt_path).name if ckpt_path else None

        model: dict[str, Any] = {
            model_key: model_name,
            "loras": [
                {"path": lora["path"], "strength": lora.get("strength", 1.0)}
                for lora in preset.loras
            ],
        }
        return {
            "model": model,
            "sampler": dict(preset.sampler),
            "negative": preset.negative,
        }

    def validate(self, preset_id: str) -> ValidationReport:
        """Check that every LoRA + the checkpoint file exists under models_root."""
        preset = self.get_preset(preset_id)
        missing: list[str] = []
        warnings: list[str] = list(preset.warnings)

        entry = self._checkpoint_entry(preset.checkpoint)
        ckpt_path = entry.get("path")
        if ckpt_path:
            full = self.models_root / ckpt_path
            if not full.exists():
                missing.append(str(full))
        else:
            warnings.append(
                f"Checkpoint for '{preset.checkpoint}' is not pinned to a file — skipped file check."
            )

        for lora in preset.loras:
            full = self.models_root / lora["path"]
            if not full.exists():
                missing.append(str(full))

        return ValidationReport(ok=not missing, missing=missing, warnings=warnings)


def _emit_json(payload: Any) -> None:
    print(json.dumps(payload, indent=2, ensure_ascii=False))


def build_selector(args: argparse.Namespace) -> LoRASelector:
    return LoRASelector(presets_path=Path(args.presets), models_root=Path(args.models_root))


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="lora_selector.py",
        description="Auto-select a ComfyUI LoRA stack + sampler settings from a text prompt.",
    )
    parser.add_argument("prompt", nargs="?", help="Scene description prompt.")
    parser.add_argument(
        "--model",
        default="auto",
        choices=["auto", "klein", "pony"],
        help="Checkpoint preference (default: auto).",
    )
    parser.add_argument(
        "--presets",
        default=str(DEFAULT_PRESETS_PATH),
        help="Path to lora_presets.yaml.",
    )
    parser.add_argument(
        "--models-root",
        default=str(DEFAULT_MODELS_ROOT),
        help="Root folder that contains loras/ and checkpoints/ (for --validate).",
    )
    parser.add_argument("--list-presets", action="store_true", help="List all preset ids and exit.")
    parser.add_argument("--validate", metavar="PRESET_ID", help="Validate a preset's files exist.")
    parser.add_argument("--analyze", metavar="PROMPT", help="Show keyword matches for a prompt.")
    parser.add_argument(
        "--patch",
        action="store_true",
        help="Emit only the deep-mergeable workflow patch instead of the full preset.",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)

    try:
        selector = build_selector(args)
    except (FileNotFoundError, ValueError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2

    if args.list_presets:
        presets = selector.list_presets()
        if _HAVE_RICH:
            console = Console(file=sys.stdout)
            for pid in presets:
                console.print(f"[bold cyan]{pid}[/bold cyan]: {selector.get_preset(pid).description}")
        else:
            for pid in presets:
                print(pid)
        return 0

    if args.validate:
        try:
            report = selector.validate(args.validate)
        except KeyError as exc:
            print(f"error: {exc}", file=sys.stderr)
            return 2
        _emit_json(report.to_dict())
        return 0 if report.ok else 1

    if args.analyze:
        _emit_json(selector.analyze(args.analyze).to_dict())
        return 0

    if not args.prompt:
        print("error: a prompt is required (or use --list-presets / --validate / --analyze).", file=sys.stderr)
        return 2

    preset = selector.select(args.prompt, checkpoint=args.model)
    patch = selector.to_workflow_patch(preset)
    if args.patch:
        _emit_json(patch)
        return 0

    output = preset.to_dict()
    output["workflow_patch"] = patch
    _emit_json(output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
