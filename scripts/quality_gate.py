#!/usr/bin/env python3
"""Two-stage production quality gate for ComfyUI generation outputs.

Pre-flight  : validate a workflow JSON before it runs -- verify that every
              referenced model / LoRA / VAE / ControlNet / IP-Adapter / CLIP
              file exists, estimate the VRAM budget and predict OOM risk.
Post-flight : analyze a produced PNG for anatomy defects (via
              :mod:`scripts.anatomy_analyzer`) plus Telegram-size compliance and
              return an ``approve | retry | reject`` verdict.

This module is a *validator only*: it never starts a ComfyUI generation.  It
reads workflow JSONs, optionally queries the local ComfyUI HTTP API for model
lookups, and always falls back to a filesystem scan when ComfyUI is offline.
"""

from __future__ import annotations

import argparse
import json
import os
import random
import sys
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

try:
    import yaml  # type: ignore
except Exception:  # pragma: no cover
    yaml = None  # type: ignore


DEFAULT_COMFY_URL = "http://127.0.0.1:8188"
DEFAULT_CONFIG = Path(__file__).resolve().parent.parent / "config" / "quality_rules.yaml"

MODEL_EXTENSIONS = (
    ".safetensors",
    ".ckpt",
    ".pth",
    ".pt",
    ".gguf",
    ".bin",
    ".onnx",
    ".sft",
)

# Loader node class -> input keys that hold a model filename.
LOADER_FILE_KEYS: Dict[str, List[str]] = {
    "CheckpointLoaderSimple": ["ckpt_name"],
    "CheckpointLoader": ["ckpt_name"],
    "UNETLoader": ["unet_name"],
    "UnetLoaderGGUF": ["unet_name"],
    "UnetLoaderGGUFAdvanced": ["unet_name"],
    "LoraLoaderModelOnly": ["lora_name"],
    "LoraLoader": ["lora_name"],
    "VAELoader": ["vae_name"],
    "ControlNetLoader": ["control_net_name"],
    "ControlNetLoaderAdvanced": ["control_net_name"],
    "IPAdapterModelLoader": ["ipadapter_file"],
    "CLIPLoader": ["clip_name"],
    "DualCLIPLoader": ["clip_name1", "clip_name2"],
    "CLIPVisionLoader": ["clip_name"],
    "UpscaleModelLoader": ["model_name"],
}

# Categories used for VRAM accounting.
MAIN_MODEL_CLASSES = {
    "CheckpointLoaderSimple",
    "CheckpointLoader",
    "UNETLoader",
    "UnetLoaderGGUF",
    "UnetLoaderGGUFAdvanced",
}
LORA_CLASSES = {"LoraLoaderModelOnly", "LoraLoader"}

# VRAM overhead constants (GB).
VAE_OVERHEAD_GB = 2.0
KSAMPLER_PEAK_GB = 1.5
LORA_OVERHEAD_GB = 0.5


# --------------------------------------------------------------------------- #
# Report dataclasses
# --------------------------------------------------------------------------- #
@dataclass
class PreFlightReport:
    ok: bool
    missing_files: List[str] = field(default_factory=list)
    found_files: List[str] = field(default_factory=list)
    vram_estimate_gb: float = 0.0
    oom_risk: str = "low"
    warnings: List[str] = field(default_factory=list)
    models_checked: List[Dict[str, Any]] = field(default_factory=list)
    source: str = "filesystem"

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class PostFlightReport:
    verdict: str
    defects: List[Dict[str, Any]] = field(default_factory=list)
    confidence: float = 0.0
    suggestions: List[str] = field(default_factory=list)
    file_size_mb: float = 0.0

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class RetryResult:
    final_verdict: str
    attempts: int
    attempts_log: List[Dict[str, Any]] = field(default_factory=list)
    suggested_seed: Optional[int] = None
    suggested_prompt_adjustments: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #
def load_rules(config_path: Optional[Path] = None) -> Dict[str, Any]:
    """Load ``quality_rules.yaml`` (falling back to sane defaults)."""

    path = config_path or DEFAULT_CONFIG
    if yaml is not None and path.exists():
        try:
            with path.open("r", encoding="utf-8") as handle:
                data = yaml.safe_load(handle) or {}
            return data.get("rules", data)
        except Exception:
            pass
    return _default_rules()


def _default_rules() -> Dict[str, Any]:
    return {
        "anatomical": {
            "extra_limbs": {"enabled": True, "confidence_threshold": 0.7},
            "fused_hands": {"enabled": True, "confidence_threshold": 0.6},
            "bad_feet": {"enabled": True, "confidence_threshold": 0.7},
            "asymmetric_breasts": {"enabled": True, "area_diff_threshold": 0.25},
            "toy_color_roulette": {
                "enabled": True,
                "hue_ranges": {"purple": [280, 320], "pink": [300, 340]},
            },
            "penetration_miss": {"enabled": True},
        },
        "pose_forbidden": [
            {"keywords": ["squat", "toy"], "action": "reject", "reason": "3-arm artifact"}
        ],
        "approved_poses": ["standing_water", "kneel_water", "shore_walk"],
        "vram": {
            "rtx3060_12gb": {
                "safe_gb": 10,
                "model_vram_gb": {
                    "flux-2-klein-9b": 6,
                    "ponyV6XL": 7,
                    "sdxl": 4,
                    "sd1.5": 2,
                    "wan2.2": 12,
                    "default": 4,
                },
            }
        },
        "telegram": {"max_size_mb": 10, "safety_threshold_mb": 8, "upscale_warning": "4x"},
    }


def estimate_model_vram(name: str, table: Dict[str, Any]) -> Tuple[float, str]:
    """Map a model filename to an estimated VRAM footprint (GB)."""

    default = float(table.get("default", 4))
    lc = name.lower()
    # Order matters: check the most specific tokens first.
    checks: List[Tuple[Tuple[str, ...], str]] = [
        (("klein", "flux-2-klein", "flux2"), "flux-2-klein-9b"),
        (("pony",), "ponyV6XL"),
        (("wan2.2", "wan22", "wan2_2", "wan"), "wan2.2"),
        (("sdxl", "juggernautxl", "_xl", "-xl"), "sdxl"),
        (("sd1.5", "sd15", "v1-5", "sd_1.5", "realvis", "realisticvision"), "sd1.5"),
    ]
    for tokens, key in checks:
        if any(tok in lc for tok in tokens):
            return float(table.get(key, default)), key
    return default, "default"


def looks_like_model_file(value: Any) -> bool:
    return isinstance(value, str) and value.lower().endswith(MODEL_EXTENSIONS)


# --------------------------------------------------------------------------- #
# QualityGate
# --------------------------------------------------------------------------- #
class QualityGate:
    def __init__(
        self,
        workflows_dir: Optional[Path] = None,
        models_roots: Optional[Sequence[Path]] = None,
        comfyui_url: str = DEFAULT_COMFY_URL,
        config_path: Optional[Path] = None,
    ) -> None:
        self.workflows_dir = Path(workflows_dir) if workflows_dir else None
        self.models_roots = [Path(p) for p in (models_roots or [])]
        self.comfyui_url = comfyui_url.rstrip("/")
        self.rules = load_rules(config_path)
        self._file_index: Optional[Dict[str, List[str]]] = None

    # -- model lookup --------------------------------------------------------- #
    def _comfy_models(self) -> Optional[set]:
        """Query the ComfyUI ``/models`` endpoint; return ``None`` when offline."""

        try:
            import httpx  # type: ignore

            resp = httpx.get(f"{self.comfyui_url}/models", timeout=3.0)
            resp.raise_for_status()
            data = resp.json()
        except Exception:
            return None
        names: set = set()

        def _collect(obj: Any) -> None:
            if isinstance(obj, str):
                names.add(os.path.basename(obj))
            elif isinstance(obj, dict):
                for value in obj.values():
                    _collect(value)
            elif isinstance(obj, (list, tuple)):
                for item in obj:
                    _collect(item)

        _collect(data)
        return names or None

    def _build_file_index(self) -> Dict[str, List[str]]:
        if self._file_index is not None:
            return self._file_index
        index: Dict[str, List[str]] = {}
        for root in self.models_roots:
            if not root.exists():
                continue
            for dirpath, _dirs, files in os.walk(root):
                for fname in files:
                    index.setdefault(fname.lower(), []).append(
                        os.path.join(dirpath, fname)
                    )
        self._file_index = index
        return index

    def _model_exists(self, filename: str, comfy_names: Optional[set]) -> bool:
        base = os.path.basename(filename.replace("\\", "/")).lower()
        if comfy_names is not None and (
            base in {n.lower() for n in comfy_names} or filename in comfy_names
        ):
            return True
        index = self._build_file_index()
        return base in index

    # -- workflow parsing ----------------------------------------------------- #
    @staticmethod
    def _load_workflow(workflow: Any) -> Dict[str, Any]:
        if isinstance(workflow, dict):
            return workflow
        path = Path(workflow)
        if not path.exists():
            raise FileNotFoundError(f"Workflow file not found: {path}")
        text = path.read_text(encoding="utf-8")
        try:
            return json.loads(text)
        except json.JSONDecodeError as exc:
            raise ValueError(
                f"{path} is not a valid workflow JSON ({exc.msg} at line {exc.lineno}). "
                "Pre-flight expects a ComfyUI workflow JSON, not a script or other file."
            ) from exc

    @staticmethod
    def _iter_loader_entries(workflow: Dict[str, Any]) -> List[Dict[str, Any]]:
        """Yield ``{class_type, files, category}`` for each loader node.

        Supports both the ComfyUI API format (``{id: {class_type, inputs}}``)
        and the UI graph format (``{"nodes": [{"type", "widgets_values"}]}``).
        """

        entries: List[Dict[str, Any]] = []

        def categorize(class_type: str) -> str:
            if class_type in MAIN_MODEL_CLASSES:
                return "model"
            if class_type in LORA_CLASSES:
                return "lora"
            return "aux"

        def add_entry(class_type: str, files: List[str]) -> None:
            files = [f for f in files if isinstance(f, str) and f.strip()]
            if not files:
                return
            entries.append(
                {"class_type": class_type, "files": files, "category": categorize(class_type)}
            )

        # UI graph format.
        if isinstance(workflow.get("nodes"), list):
            for node in workflow["nodes"]:
                if not isinstance(node, dict):
                    continue
                class_type = node.get("type") or node.get("class_type")
                if class_type not in LOADER_FILE_KEYS:
                    continue
                widgets = node.get("widgets_values") or []
                if isinstance(widgets, dict):
                    widgets = list(widgets.values())
                files = [w for w in widgets if looks_like_model_file(w)]
                add_entry(class_type, files)
            return entries

        # API format.
        for node in workflow.values():
            if not isinstance(node, dict):
                continue
            class_type = node.get("class_type")
            if class_type not in LOADER_FILE_KEYS:
                continue
            inputs = node.get("inputs", {}) or {}
            files: List[str] = []
            for key in LOADER_FILE_KEYS[class_type]:
                value = inputs.get(key)
                if isinstance(value, str):
                    files.append(value)
            # Fallback: some exports flatten filenames as bare strings.
            if not files:
                files = [v for v in inputs.values() if looks_like_model_file(v)]
            add_entry(class_type, files)
        return entries

    @staticmethod
    def _has_ksampler(workflow: Dict[str, Any]) -> bool:
        if isinstance(workflow.get("nodes"), list):
            return any(
                isinstance(n, dict) and str(n.get("type", "")).startswith("KSampler")
                for n in workflow["nodes"]
            )
        return any(
            isinstance(n, dict) and str(n.get("class_type", "")).startswith("KSampler")
            for n in workflow.values()
        )

    # -- preflight ------------------------------------------------------------ #
    def preflight(self, workflow: Any) -> PreFlightReport:
        wf = self._load_workflow(workflow)
        comfy_names = self._comfy_models()
        source = "comfyui" if comfy_names is not None else "filesystem"

        entries = self._iter_loader_entries(wf)
        vram_table = (
            self.rules.get("vram", {}).get("rtx3060_12gb", {}).get("model_vram_gb", {})
        )
        safe_gb = float(self.rules.get("vram", {}).get("rtx3060_12gb", {}).get("safe_gb", 10))

        missing: List[str] = []
        found: List[str] = []
        warnings: List[str] = []
        models_checked: List[Dict[str, Any]] = []
        base_vram = 0.0
        lora_count = 0

        for entry in entries:
            for fname in entry["files"]:
                exists = self._model_exists(fname, comfy_names)
                base = os.path.basename(fname.replace("\\", "/"))
                (found if exists else missing).append(base)
                record: Dict[str, Any] = {
                    "class_type": entry["class_type"],
                    "file": base,
                    "exists": exists,
                    "category": entry["category"],
                }
                if entry["category"] == "model":
                    vram, matched = estimate_model_vram(base, vram_table)
                    base_vram += vram
                    record["vram_gb"] = vram
                    record["vram_key"] = matched
                elif entry["category"] == "lora":
                    lora_count += 1
                models_checked.append(record)

        if not entries:
            warnings.append("No recognized model loader nodes found in workflow.")

        vram_total = base_vram + lora_count * LORA_OVERHEAD_GB + VAE_OVERHEAD_GB
        if self._has_ksampler(wf):
            vram_total += KSAMPLER_PEAK_GB

        if vram_total > safe_gb:
            oom_risk = "high"
            warnings.append(
                f"Estimated VRAM {vram_total:.1f} GB exceeds safe budget {safe_gb:.0f} GB "
                "on RTX 3060 12GB -- OOM likely."
            )
        elif vram_total > safe_gb - 2:
            oom_risk = "medium"
            warnings.append(
                f"Estimated VRAM {vram_total:.1f} GB is close to the {safe_gb:.0f} GB budget."
            )
        else:
            oom_risk = "low"

        for base in missing:
            warnings.append(f"Missing model file: {base}")

        return PreFlightReport(
            ok=len(missing) == 0,
            missing_files=missing,
            found_files=found,
            vram_estimate_gb=round(vram_total, 2),
            oom_risk=oom_risk,
            warnings=warnings,
            models_checked=models_checked,
            source=source,
        )

    # -- postflight ----------------------------------------------------------- #
    def _forbidden_pose(self, prompt: str) -> Optional[Dict[str, Any]]:
        lc = (prompt or "").lower()
        for rule in self.rules.get("pose_forbidden", []):
            keywords = rule.get("keywords", [])
            if keywords and all(k.lower() in lc for k in keywords):
                return rule
        return None

    def postflight(self, image_path: Any, prompt: str) -> PostFlightReport:
        image_path = Path(image_path)
        defects: List[Dict[str, Any]] = []
        suggestions: List[str] = []

        telegram = self.rules.get("telegram", {})
        max_mb = float(telegram.get("max_size_mb", 10))
        safety_mb = float(telegram.get("safety_threshold_mb", 8))

        size_mb = 0.0
        if image_path.exists():
            size_mb = image_path.stat().st_size / (1024 * 1024)

        forbidden = self._forbidden_pose(prompt)
        if forbidden:
            defects.append(
                {
                    "type": "pose_forbidden",
                    "detected": True,
                    "confidence": 0.99,
                    "reason": forbidden.get("reason", "forbidden pose combination"),
                    "severity": "reject",
                }
            )
            suggestions.append(
                "Prompt combines forbidden keywords "
                f"{forbidden.get('keywords')}: {forbidden.get('reason')}. "
                "Regenerate with a different pose or drop the toy."
            )

        # Anatomy analysis (best-effort; failures degrade to no anatomy defects).
        anatomical_rules = self.rules.get("anatomical", {})
        analysis_error: Optional[str] = None
        if image_path.exists():
            try:
                import cv2  # type: ignore

                try:
                    from scripts.anatomy_analyzer import AnatomyAnalyzer
                except ModuleNotFoundError:
                    from anatomy_analyzer import AnatomyAnalyzer  # type: ignore

                image = cv2.imread(str(image_path))
                if image is None:
                    raise ValueError("cv2 could not decode image")
                analyzer = AnatomyAnalyzer(image, prompt, rules=anatomical_rules)
                findings = analyzer.run_all()
                for name, finding in findings.items():
                    if not finding.get("detected"):
                        continue
                    threshold = self._confidence_threshold(anatomical_rules, name)
                    if finding.get("confidence", 0.0) < threshold:
                        continue
                    defects.append(
                        {
                            "type": name,
                            "detected": True,
                            "confidence": round(float(finding["confidence"]), 3),
                            "severity": self._defect_severity(name),
                            **{
                                k: v
                                for k, v in finding.items()
                                if k not in ("detected", "confidence")
                            },
                        }
                    )
            except Exception as exc:  # noqa: BLE001
                analysis_error = str(exc)
        else:
            analysis_error = f"Image not found: {image_path}"

        if analysis_error:
            defects.append(
                {
                    "type": "analysis_unavailable",
                    "detected": False,
                    "confidence": 0.0,
                    "severity": "info",
                    "detail": analysis_error,
                }
            )
            suggestions.append(f"Anatomy analysis skipped: {analysis_error}")

        # Telegram-size compliance.
        if size_mb >= max_mb:
            defects.append(
                {
                    "type": "telegram_oversize",
                    "detected": True,
                    "confidence": 1.0,
                    "severity": "reject",
                    "size_mb": round(size_mb, 2),
                }
            )
            suggestions.append(
                f"Output is {size_mb:.1f} MB (>= {max_mb:.0f} MB Telegram limit); "
                "re-export at 2x upscale instead of 4x."
            )
        elif size_mb > safety_mb:
            defects.append(
                {
                    "type": "telegram_size_warning",
                    "detected": True,
                    "confidence": 0.8,
                    "severity": "retry",
                    "size_mb": round(size_mb, 2),
                }
            )
            suggestions.append(
                f"Output is {size_mb:.1f} MB (> {safety_mb:.0f} MB); recommend 2x resize "
                "to stay comfortably under the Telegram limit."
            )

        verdict, confidence = self._decide_verdict(defects)
        if verdict != "approve" and not any(
            "seed" in s.lower() for s in suggestions
        ):
            suggestions.append("Retry generation with a different KSampler seed.")

        return PostFlightReport(
            verdict=verdict,
            defects=defects,
            confidence=round(confidence, 3),
            suggestions=suggestions,
            file_size_mb=round(size_mb, 2),
        )

    @staticmethod
    def _confidence_threshold(rules: Dict[str, Any], name: str) -> float:
        rule = rules.get(name, {}) or {}
        return float(rule.get("confidence_threshold", 0.5))

    @staticmethod
    def _defect_severity(name: str) -> str:
        reject_defects = {"extra_limbs", "penetration_miss"}
        if name in reject_defects:
            return "reject"
        return "retry"

    @staticmethod
    def _decide_verdict(defects: List[Dict[str, Any]]) -> Tuple[str, float]:
        active = [d for d in defects if d.get("detected")]
        if not active:
            return "approve", 0.9
        severities = {d.get("severity") for d in active}
        max_conf = max((float(d.get("confidence", 0.0)) for d in active), default=0.0)
        if "reject" in severities:
            return "reject", max_conf
        if "retry" in severities:
            return "retry", max_conf
        return "approve", 0.9

    # -- auto retry ----------------------------------------------------------- #
    def auto_retry(
        self,
        image_path: Any,
        prompt: str,
        workflow: Dict[str, Any],
        *,
        max_attempts: int = 3,
    ) -> RetryResult:
        attempts_log: List[Dict[str, Any]] = []

        # Forbidden pose -> reject immediately, never loop.
        forbidden = self._forbidden_pose(prompt)
        if forbidden:
            report = self.postflight(image_path, prompt)
            attempts_log.append(
                {
                    "attempt": 1,
                    "verdict": "reject",
                    "reason": forbidden.get("reason"),
                    "defects": report.defects,
                }
            )
            adjustments = [
                f"Remove conflicting keyword(s) {forbidden.get('keywords')} "
                f"({forbidden.get('reason')}).",
                "Pick an approved pose: "
                + ", ".join(self.rules.get("approved_poses", [])),
            ]
            return RetryResult(
                final_verdict="reject",
                attempts=1,
                attempts_log=attempts_log,
                suggested_seed=None,
                suggested_prompt_adjustments=adjustments,
            )

        suggested_seed: Optional[int] = None
        adjustments: List[str] = []
        final_verdict = "reject"
        working_workflow = json.loads(json.dumps(workflow)) if workflow else {}

        for attempt in range(1, max(1, max_attempts) + 1):
            report = self.postflight(image_path, prompt)
            attempts_log.append(
                {
                    "attempt": attempt,
                    "verdict": report.verdict,
                    "defects": report.defects,
                    "seed": suggested_seed,
                }
            )
            final_verdict = report.verdict
            if report.verdict != "reject":
                break
            if attempt >= max_attempts:
                break
            # Propose a new seed for the caller to re-run ComfyUI with.
            suggested_seed = random.randint(1, 2**31 - 1)
            self._apply_seed(working_workflow, suggested_seed)
            adjustments.extend(
                s for s in report.suggestions if s not in adjustments
            )

        return RetryResult(
            final_verdict=final_verdict,
            attempts=len(attempts_log),
            attempts_log=attempts_log,
            suggested_seed=suggested_seed,
            suggested_prompt_adjustments=adjustments,
        )

    @staticmethod
    def _apply_seed(workflow: Dict[str, Any], seed: int) -> None:
        if isinstance(workflow.get("nodes"), list):
            for node in workflow["nodes"]:
                if isinstance(node, dict) and str(node.get("type", "")).startswith("KSampler"):
                    widgets = node.get("widgets_values")
                    if isinstance(widgets, list) and widgets:
                        widgets[0] = seed
            return
        for node in workflow.values():
            if isinstance(node, dict) and str(node.get("class_type", "")).startswith("KSampler"):
                node.setdefault("inputs", {})["seed"] = seed


# --------------------------------------------------------------------------- #
# CLI
# --------------------------------------------------------------------------- #
def _default_models_roots() -> List[Path]:
    return [
        Path(r"E:\AI\models\comfyui"),
        Path(r"D:\AI\Models\comfyui"),
        Path(r"C:\Users\user\comfyui\ComfyUI_windows_portable\ComfyUI\models"),
    ]


def _cmd_preflight(args: argparse.Namespace) -> int:
    gate = QualityGate(
        models_roots=[Path(p) for p in args.models_root] or _default_models_roots(),
        comfyui_url=args.comfy_url,
    )
    try:
        report = gate.preflight(Path(args.workflow))
    except (FileNotFoundError, ValueError) as exc:
        print(json.dumps({"ok": False, "error": str(exc)}, indent=2, ensure_ascii=False))
        return 1
    print(json.dumps(report.to_dict(), indent=2, ensure_ascii=False))
    return 0 if report.ok else 1


def _cmd_postflight(args: argparse.Namespace) -> int:
    gate = QualityGate(
        models_roots=[Path(p) for p in args.models_root],
        comfyui_url=args.comfy_url,
    )
    report = gate.postflight(Path(args.image), args.prompt)
    print(json.dumps(report.to_dict(), indent=2, ensure_ascii=False))
    return 0 if report.verdict == "approve" else 1


def _cmd_batch(args: argparse.Namespace) -> int:
    gate = QualityGate(
        models_roots=[Path(p) for p in args.models_root],
        comfyui_url=args.comfy_url,
    )
    directory = Path(args.dir)
    if not directory.exists():
        print(json.dumps({"error": f"Directory not found: {directory}"}, indent=2))
        return 1
    prompts = _load_prompt_file(Path(args.prompt_file)) if args.prompt_file else {}
    default_prompt = args.prompt or ""

    images = sorted(
        p for p in directory.rglob("*.png") if p.is_file()
    )
    results: List[Dict[str, Any]] = []
    tally = {"approve": 0, "retry": 0, "reject": 0}
    for img in images:
        prompt = prompts.get(img.name, default_prompt)
        report = gate.postflight(img, prompt)
        tally[report.verdict] = tally.get(report.verdict, 0) + 1
        results.append(
            {
                "image": str(img),
                "verdict": report.verdict,
                "confidence": report.confidence,
                "defects": [d["type"] for d in report.defects if d.get("detected")],
            }
        )
    summary = {
        "processed": len(results),
        "tally": tally,
        "results": results,
    }
    print(json.dumps(summary, indent=2, ensure_ascii=False))
    return 0 if tally.get("reject", 0) == 0 else 1


def _load_prompt_file(path: Path) -> Dict[str, str]:
    """Parse a prompt file.

    Supports ``filename.png<TAB>prompt`` / ``filename.png | prompt`` lines, or
    a JSON object mapping filename -> prompt.
    """

    if not path.exists():
        return {}
    text = path.read_text(encoding="utf-8")
    try:
        data = json.loads(text)
        if isinstance(data, dict):
            return {str(k): str(v) for k, v in data.items()}
    except json.JSONDecodeError:
        pass
    mapping: Dict[str, str] = {}
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        for sep in ("\t", "|", "="):
            if sep in line:
                name, prompt = line.split(sep, 1)
                mapping[name.strip()] = prompt.strip()
                break
    return mapping


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="ComfyUI production quality gate (pre-flight + post-flight)."
    )
    parser.add_argument(
        "--comfy-url", default=DEFAULT_COMFY_URL, help="ComfyUI HTTP API base URL."
    )
    parser.add_argument(
        "--models-root",
        action="append",
        default=[],
        help="Model root directory (repeatable).",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    p_pre = sub.add_parser("preflight", help="Validate a workflow JSON.")
    p_pre.add_argument("workflow", help="Path to the workflow JSON.")
    p_pre.set_defaults(func=_cmd_preflight)

    p_post = sub.add_parser("postflight", help="Analyze a produced PNG.")
    p_post.add_argument("image", help="Path to the PNG.")
    p_post.add_argument("prompt", nargs="?", default="", help="Generation prompt.")
    p_post.set_defaults(func=_cmd_postflight)

    p_batch = sub.add_parser("batch", help="Batch operations.")
    batch_sub = p_batch.add_subparsers(dest="batch_command", required=True)
    p_batch_post = batch_sub.add_parser("postflight", help="Batch post-flight over a directory.")
    p_batch_post.add_argument("--dir", required=True, help="Directory of PNGs.")
    p_batch_post.add_argument("--prompt-file", default=None, help="Optional prompt mapping file.")
    p_batch_post.add_argument("--prompt", default="", help="Default prompt for all images.")
    p_batch_post.set_defaults(func=_cmd_batch)

    return parser


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
