#!/usr/bin/env python3
"""Wan2.2 VACE stripe A/B harness: run ComfyUI matrix cases and score banding."""

from __future__ import annotations

import argparse
import copy
import json
import sys
import time
import uuid
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Iterable

try:
    from scripts.banding_detector import analyze_video
except ImportError:  # pragma: no cover - standalone from scripts/
    from banding_detector import analyze_video

try:
    import requests
except ImportError:  # pragma: no cover
    requests = None  # type: ignore

DEFAULT_COMFY_URL = "http://127.0.0.1:8188"
DEFAULT_REPORT_PATH = Path("memory/vace_ab_report.json")
DEFAULT_OUTPUT_DIR = Path("output/vace_ab")

# ComfyUI prompt node ids (recovered defective Wan2.2 VACE workflow).
NODE_T5 = "1"
NODE_VAE = "2"
NODE_TEXT_POS = "3"
NODE_TEXT_NEG = "4"
NODE_HIGH_LOADER = "5"
NODE_LOW_LOADER = "6"
NODE_BLOCKSWAP_HIGH = "7"
NODE_BLOCKSWAP_LOW = "8"
NODE_MODEL_HIGH = "9"
NODE_MODEL_LOW = "10"
NODE_VACE = "11"
NODE_T2V_ENCODE = "15"
NODE_IMAGE_EMBEDS = "16"
NODE_HIGH_SAMPLER = "12"
NODE_LOW_SAMPLER = "18"
NODE_DECODE = "20"
NODE_RESIZE = "30"
NODE_SAVE = "41"

BASELINE: dict[str, dict[str, Any]] = {
    NODE_T5: {
        "class_type": "LoadWanVideoT5TextEncoder",
        "inputs": {
            "model_name": "umt5_xxl_fp8_e4m3fn_scaled.safetensors",
            "precision": "bf16",
        },
    },
    NODE_VAE: {
        "class_type": "WanVideoVAELoader",
        "inputs": {"model_name": "wan_2.1_vae.safetensors", "precision": "bf16"},
    },
    NODE_TEXT_POS: {
        "class_type": "WanVideoTextEncode",
        "inputs": {
            "t5": [NODE_T5, 0],
            "positive_prompt": (
                "A senior couple walking on a beach at sunset, cinematic side view, "
                "gentle sea waves, natural motion."
            ),
            "negative_prompt": (
                "oversaturated, static, blurry, worst quality, low quality, "
                "subtitles, watermark"
            ),
            "force_offload": True,
        },
    },
    NODE_TEXT_NEG: {
        "class_type": "WanVideoTextEncode",
        "inputs": {
            "t5": [NODE_T5, 0],
            "positive_prompt": "",
            "negative_prompt": (
                "oversaturated, static, blurry, worst quality, low quality, "
                "subtitles, watermark"
            ),
            "force_offload": True,
        },
    },
    NODE_HIGH_LOADER: {
        "class_type": "WanVideoModelLoader",
        "inputs": {
            "model": "Wan2_2-I2V-A14B-HIGH_fp8_e4m3fn_scaled_KJ.safetensors",
            "base_precision": "fp16_fast",
            "quantization": "fp8_e4m3fn_scaled",
            "load_device": "offload_device",
            "attention_mode": "sageattn",
        },
    },
    NODE_LOW_LOADER: {
        "class_type": "WanVideoModelLoader",
        "inputs": {
            "model": "Wan2_2-I2V-A14B-LOW_fp8_e4m3fn_scaled_KJ.safetensors",
            "base_precision": "fp16_fast",
            "quantization": "fp8_e4m3fn_scaled",
            "load_device": "offload_device",
            "attention_mode": "sageattn",
        },
    },
    NODE_BLOCKSWAP_HIGH: {
        "class_type": "WanVideoBlockSwap",
        "inputs": {"blocks_to_swap": 20, "offload_img_emb": False, "offload_txt_emb": False},
    },
    NODE_BLOCKSWAP_LOW: {
        "class_type": "WanVideoBlockSwap",
        "inputs": {"blocks_to_swap": 20, "offload_img_emb": False, "offload_txt_emb": False},
    },
    NODE_MODEL_HIGH: {
        "class_type": "WanVideoSetBlockSwap",
        "inputs": {"model": [NODE_HIGH_LOADER, 0], "block_swap_args": [NODE_BLOCKSWAP_HIGH, 0]},
    },
    NODE_MODEL_LOW: {
        "class_type": "WanVideoSetBlockSwap",
        "inputs": {"model": [NODE_LOW_LOADER, 0], "block_swap_args": [NODE_BLOCKSWAP_LOW, 0]},
    },
    NODE_RESIZE: {
        "class_type": "ImageResizeKJ",
        "inputs": {
            "image": ["14", 0],
            "width": 832,
            "height": 480,
            "divisible_by": 16,
            "crop": "disabled",
        },
    },
    NODE_VACE: {
        "class_type": "WanVideoVACEEncode",
        "inputs": {
            "vae": [NODE_VAE, 0],
            "width": 832,
            "height": 480,
            "num_frames": 81,
            "strength": 1.0,
            "vace_start_percent": 0.0,
            "vace_end_percent": 1.0,
            "input_frames": [NODE_RESIZE, 0],
            "tiled_vae": False,
        },
    },
    "14": {
        "class_type": "VHS_LoadVideo",
        "inputs": {
            "video": "vace_source.mp4",
            "force_rate": 16,
            "custom_width": 0,
            "custom_height": 0,
            "frame_load_cap": 81,
            "skip_first_frames": 0,
            "select_every_nth": 1,
        },
    },
    NODE_T2V_ENCODE: {
        "class_type": "WanVideoImageToVideoEncode",
        "inputs": {
            "vae": [NODE_VAE, 0],
            "width": 832,
            "height": 480,
            "num_frames": 81,
            "noise_aug_strength": 0.0,
            "start_latent_strength": 1.0,
            "end_latent_strength": 1.0,
            "force_offload": True,
            "start_image": [NODE_RESIZE, 0],
            "tiled_vae": False,
        },
    },
    NODE_IMAGE_EMBEDS: {
        "class_type": "WanVideoCombineEmbeds",
        "inputs": {
            "t2v_embeds": [NODE_T2V_ENCODE, 0],
            "vace_embeds": [NODE_VACE, 0],
        },
    },
    NODE_HIGH_SAMPLER: {
        "class_type": "WanVideoSampler",
        "inputs": {
            "model": [NODE_MODEL_HIGH, 0],
            "image_embeds": [NODE_IMAGE_EMBEDS, 0],
            "steps": 6,
            "cfg": 1.0,
            "shift": 8.0,
            "seed": 42,
            "force_offload": True,
            "scheduler": "dpm++_sde",
            "riflex_freq_index": 0,
            "text_embeds": [NODE_TEXT_POS, 0],
            "start_step": 0,
            "end_step": 3,
        },
    },
    NODE_LOW_SAMPLER: {
        "class_type": "WanVideoSampler",
        "inputs": {
            "model": [NODE_MODEL_LOW, 0],
            "image_embeds": [NODE_IMAGE_EMBEDS, 0],
            "steps": 6,
            "cfg": 1.0,
            "shift": 8.0,
            "seed": 42,
            "force_offload": True,
            "scheduler": "dpm++_sde",
            "riflex_freq_index": 0,
            "text_embeds": [NODE_TEXT_POS, 0],
            "samples": [NODE_HIGH_SAMPLER, 0],
            "start_step": 3,
            "end_step": -1,
        },
    },
    NODE_DECODE: {
        "class_type": "WanVideoDecode",
        "inputs": {
            "vae": [NODE_VAE, 0],
            "samples": [NODE_LOW_SAMPLER, 0],
            "enable_vae_tiling": False,
            "tile_x": 272,
            "tile_y": 272,
            "tile_stride_x": 144,
            "tile_stride_y": 128,
        },
    },
    NODE_SAVE: {
        "class_type": "VHS_VideoCombine",
        "inputs": {
            "images": [NODE_DECODE, 0],
            "frame_rate": 16,
            "loop_count": 0,
            "filename_prefix": "wv_vace_final",
            "format": "video/h264-mp4",
            "pix_fmt": "yuv420p",
            "crf": 19,
            "save_metadata": True,
            "trim_to_audio": False,
            "pingpong": False,
            "save_output": True,
        },
    },
}

CASES: dict[str, dict[str, Any]] = {
    "baseline": {},
    "cfg_1": {"cfg": 1.0},
    "cfg_3_5": {"cfg": 3.5},
    "steps_8": {"steps": 8},
    "split_4": {"split": 4},
    "scheduler_euler": {"scheduler": "euler"},
    "shift_5": {"shift": 5.0},
    "vace_strength_0_75": {"vace_strength": 0.75},
    "vace_end_0_8": {"vace_end_percent": 0.8},
    "divisible_32": {"divisible_by": 32},
    "attention_sdpa": {"attention_mode": "sdpa"},
    "quant_fp8_e4m3fn": {"quantization": "fp8_e4m3fn"},
}

CASE_DESCRIPTIONS: dict[str, str] = {
    "baseline": "Recovered defective prompt (no overrides).",
    "cfg_1": "CFG on both samplers (explicit 1.0).",
    "cfg_3_5": "Higher CFG on both samplers.",
    "steps_8": "More denoise steps on both samplers.",
    "split_4": "Earlier high/low noise handoff (split step 4).",
    "scheduler_euler": "Euler scheduler on both samplers.",
    "shift_5": "Lower sampling shift on both samplers.",
    "vace_strength_0_75": "Reduced VACE strength.",
    "vace_end_0_8": "VACE active for first 80% of steps only.",
    "divisible_32": "Resize divisible_by 32 instead of 16.",
    "attention_sdpa": "SDPA attention on both model loaders.",
    "quant_fp8_e4m3fn": "fp8_e4m3fn quantization on both loaders.",
}


@dataclass
class GraphIssue:
    kind: str
    message: str


@dataclass
class CaseResult:
    case: str
    overrides: dict[str, Any]
    prompt_id: str | None = None
    video_path: str | None = None
    banding: dict[str, Any] = field(default_factory=dict)
    error: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def iter_node_refs(value: Any) -> Iterable[tuple[str, int]]:
    """Yield (node_id, slot) pairs from a ComfyUI input value."""
    if isinstance(value, list) and len(value) == 2 and isinstance(value[0], str):
        if isinstance(value[1], int):
            yield value[0], value[1]
    elif isinstance(value, list):
        for item in value:
            yield from iter_node_refs(item)
    elif isinstance(value, dict):
        for item in value.values():
            yield from iter_node_refs(item)


def validate_graph(prompt: dict[str, dict[str, Any]]) -> list[GraphIssue]:
    """Return structural issues: dangling refs and directed cycles."""
    issues: list[GraphIssue] = []
    node_ids = set(prompt)

    for nid, node in prompt.items():
        inputs = node.get("inputs", {})
        for key, val in inputs.items():
            for ref_id, _slot in iter_node_refs(val):
                if ref_id not in node_ids:
                    issues.append(
                        GraphIssue(
                            "dangling_ref",
                            f"node {nid} input {key} references missing node {ref_id}",
                        )
                    )

    # Cycle detection on node dependency graph.
    graph: dict[str, set[str]] = {nid: set() for nid in prompt}
    for nid, node in prompt.items():
        for ref_id, _ in iter_node_refs(node.get("inputs", {})):
            if ref_id in graph:
                graph[nid].add(ref_id)

    visiting: set[str] = set()
    visited: set[str] = set()

    def dfs(nid: str, stack: list[str]) -> None:
        if nid in visiting:
            issues.append(
                GraphIssue("cycle", " -> ".join(stack + [nid]))
            )
            return
        if nid in visited:
            return
        visiting.add(nid)
        for dep in graph.get(nid, ()):
            dfs(dep, stack + [nid])
        visiting.remove(nid)
        visited.add(nid)

    for nid in prompt:
        if nid not in visited:
            dfs(nid, [])

    return issues


def apply_overrides(
    prompt: dict[str, dict[str, Any]], overrides: dict[str, Any]
) -> dict[str, dict[str, Any]]:
    """Return a deep-copied prompt with matrix overrides applied."""
    out = copy.deepcopy(prompt)
    if not overrides:
        return out

    if "cfg" in overrides:
        cfg = float(overrides["cfg"])
        out[NODE_HIGH_SAMPLER]["inputs"]["cfg"] = cfg
        out[NODE_LOW_SAMPLER]["inputs"]["cfg"] = cfg

    if "steps" in overrides:
        steps = int(overrides["steps"])
        out[NODE_HIGH_SAMPLER]["inputs"]["steps"] = steps
        out[NODE_LOW_SAMPLER]["inputs"]["steps"] = steps

    if "split" in overrides:
        split = int(overrides["split"])
        out[NODE_HIGH_SAMPLER]["inputs"]["start_step"] = 0
        out[NODE_HIGH_SAMPLER]["inputs"]["end_step"] = split
        out[NODE_LOW_SAMPLER]["inputs"]["start_step"] = split
        out[NODE_LOW_SAMPLER]["inputs"]["end_step"] = -1

    if "scheduler" in overrides:
        sched = str(overrides["scheduler"])
        out[NODE_HIGH_SAMPLER]["inputs"]["scheduler"] = sched
        out[NODE_LOW_SAMPLER]["inputs"]["scheduler"] = sched

    if "shift" in overrides:
        shift = float(overrides["shift"])
        out[NODE_HIGH_SAMPLER]["inputs"]["shift"] = shift
        out[NODE_LOW_SAMPLER]["inputs"]["shift"] = shift

    if "vace_strength" in overrides:
        out[NODE_VACE]["inputs"]["strength"] = float(overrides["vace_strength"])

    if "vace_end_percent" in overrides:
        out[NODE_VACE]["inputs"]["vace_end_percent"] = float(overrides["vace_end_percent"])

    if "divisible_by" in overrides:
        out[NODE_RESIZE]["inputs"]["divisible_by"] = int(overrides["divisible_by"])

    if "attention_mode" in overrides:
        mode = str(overrides["attention_mode"])
        out[NODE_HIGH_LOADER]["inputs"]["attention_mode"] = mode
        out[NODE_LOW_LOADER]["inputs"]["attention_mode"] = mode

    if "quantization" in overrides:
        quant = str(overrides["quantization"])
        out[NODE_HIGH_LOADER]["inputs"]["quantization"] = quant
        out[NODE_LOW_LOADER]["inputs"]["quantization"] = quant

    return out


def build_prompt(case: str) -> dict[str, dict[str, Any]]:
    if case not in CASES:
        raise KeyError(f"unknown case: {case}")
    return apply_overrides(BASELINE, CASES[case])


class ComfyRunner:
    def __init__(self, base_url: str = DEFAULT_COMFY_URL, timeout_sec: int = 1800) -> None:
        if requests is None:
            raise RuntimeError("requests is required to talk to ComfyUI")
        self.base_url = base_url.rstrip("/")
        self.timeout_sec = timeout_sec
        self.client_id = str(uuid.uuid4())
        self.session = requests.Session()

    def _request(self, method: str, path: str, **kwargs: Any) -> requests.Response:
        url = f"{self.base_url}{path}"
        response = self.session.request(method, url, timeout=120, **kwargs)
        response.raise_for_status()
        return response

    def queue_prompt(self, prompt: dict[str, dict[str, Any]]) -> str:
        payload = {"prompt": prompt, "client_id": self.client_id}
        data = self._request("POST", "/prompt", json=payload).json()
        prompt_id = data.get("prompt_id")
        if not prompt_id:
            raise RuntimeError(f"ComfyUI did not return prompt_id: {data}")
        return str(prompt_id)

    def wait_history(self, prompt_id: str) -> dict[str, Any]:
        start = time.time()
        while time.time() - start < self.timeout_sec:
            history = self._request("GET", f"/history/{prompt_id}").json()
            if prompt_id in history:
                return history[prompt_id]
            time.sleep(1.0)
        raise TimeoutError(f"timed out waiting for prompt {prompt_id}")

    def find_output_video(self, history: dict[str, Any]) -> Path | None:
        outputs = history.get("outputs", {})
        for node_out in outputs.values():
            for key in ("gifs", "videos"):
                for item in node_out.get(key, []) or []:
                    filename = item.get("filename")
                    if not filename:
                        continue
                    subfolder = item.get("subfolder", "")
                    folder_type = item.get("type", "output")
                    return Path(folder_type) / subfolder / filename
        return None


def score_video(path: Path, fps: float = 2.0) -> dict[str, Any]:
    return analyze_video(path, fps=fps)


def run_case(
    case: str,
    *,
    comfy_url: str = DEFAULT_COMFY_URL,
    output_dir: Path = DEFAULT_OUTPUT_DIR,
    fps: float = 2.0,
) -> CaseResult:
    overrides = CASES[case]
    result = CaseResult(case=case, overrides=dict(overrides))
    try:
        prompt = build_prompt(case)
        issues = validate_graph(prompt)
        if issues:
            raise RuntimeError(
                "; ".join(f"{i.kind}: {i.message}" for i in issues)
            )
        runner = ComfyRunner(comfy_url)
        prompt_id = runner.queue_prompt(prompt)
        result.prompt_id = prompt_id
        history = runner.wait_history(prompt_id)
        rel = runner.find_output_video(history)
        if rel is None:
            raise RuntimeError("no video output in ComfyUI history")
        video_path = Path("ComfyUI") / rel
        if not video_path.is_file():
            video_path = output_dir / rel.name
        result.video_path = str(video_path)
        result.banding = score_video(video_path, fps=fps)
    except Exception as exc:  # noqa: BLE001 - surfaced in report JSON
        result.error = str(exc)
    return result


def load_report(path: Path) -> list[dict[str, Any]]:
    if not path.is_file():
        return []
    data = json.loads(path.read_text(encoding="utf-8"))
    if isinstance(data, list):
        return data
    return data.get("results", [])


def save_report(path: Path, results: list[CaseResult]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "results": [r.to_dict() for r in results],
    }
    path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")


def format_report_table(rows: list[dict[str, Any]]) -> str:
    lines = [
        f"{'case':<22} {'verdict':<10} {'green_dom':>10} {'score':>8} {'peak':>8}",
        "-" * 62,
    ]
    for row in rows:
        band = row.get("banding") or {}
        lines.append(
            f"{row.get('case', ''):<22} "
            f"{band.get('verdict', row.get('error', '?')):<10} "
            f"{band.get('green_dominance', 0):>10.2f} "
            f"{band.get('score', 0):>8.1f} "
            f"{band.get('periodic_best_corr', 0):>8.3f}"
        )
    return "\n".join(lines)


def list_cases() -> None:
    for name, overrides in CASES.items():
        desc = CASE_DESCRIPTIONS.get(name, "")
        keys = ", ".join(sorted(overrides)) if overrides else "(none)"
        print(f"{name:22} overrides={keys}  {desc}")


def _main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Wan2.2 VACE stripe A/B harness (ComfyUI + banding_detector)."
    )
    parser.add_argument("--list", action="store_true", help="List experiment cases.")
    parser.add_argument("--run", metavar="CASE", help="Queue one case on ComfyUI and score.")
    parser.add_argument("--matrix", action="store_true", help="Run every case sequentially.")
    parser.add_argument("--report", action="store_true", help="Print saved report table.")
    parser.add_argument("--comfy-url", default=DEFAULT_COMFY_URL, dest="comfy_url")
    parser.add_argument("--report-path", type=Path, default=DEFAULT_REPORT_PATH)
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR)
    parser.add_argument("--fps", type=float, default=2.0, help="Banding detector video FPS.")
    args = parser.parse_args(argv)

    if args.list:
        list_cases()
        return 0

    if args.report:
        rows = load_report(args.report_path)
        if not rows:
            print(f"No report at {args.report_path}", file=sys.stderr)
            return 1
        print(format_report_table(rows))
        return 0

    if args.run:
        result = run_case(
            args.run,
            comfy_url=args.comfy_url,
            output_dir=args.output_dir,
            fps=args.fps,
        )
        save_report(args.report_path, [result])
        if result.error:
            print(result.error, file=sys.stderr)
            return 1
        print(json.dumps(result.to_dict(), indent=2))
        return 0

    if args.matrix:
        results: list[CaseResult] = []
        for case in CASES:
            print(f"Running {case}...", file=sys.stderr)
            results.append(
                run_case(
                    case,
                    comfy_url=args.comfy_url,
                    output_dir=args.output_dir,
                    fps=args.fps,
                )
            )
        save_report(args.report_path, results)
        print(format_report_table([r.to_dict() for r in results]))
        return 0

    parser.print_help()
    return 1


if __name__ == "__main__":
    raise SystemExit(_main())
