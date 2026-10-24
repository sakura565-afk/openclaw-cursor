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

_REPO_ROOT = Path(__file__).resolve().parent.parent
_FIXTURE_REL = Path("tests/fixtures/vace_defective_workflow.json")

try:
    import requests
except ImportError:  # pragma: no cover
    requests = None  # type: ignore


def _analyze_video(path: Path, fps: float = 2.0) -> dict[str, Any]:
    try:
        from scripts.banding_detector import analyze_video
    except ImportError:  # pragma: no cover - standalone from scripts/
        from banding_detector import analyze_video
    return analyze_video(path, fps=fps)


DEFAULT_COMFY_URL = "http://127.0.0.1:8188"
DEFAULT_REPORT_PATH = Path("memory/vace_ab_report.json")
DEFAULT_OUTPUT_DIR = Path("output/vace_ab")

# ComfyUI prompt node ids (recovered defective Wan2.2 Fun VACE workflow).
NODE_HIGH_LOADER = "1"
NODE_LOW_LOADER = "2"
NODE_T5 = "5"
NODE_VACE = "6"
NODE_HIGH_SAMPLER = "7"
NODE_LOW_SAMPLER = "8"
NODE_BLOCK_SWAP = "3"
NODE_VAE = "9"
NODE_DECODE = "10"
NODE_RESIZE = "33"
NODE_SAVE = "41"


def _fixture_path() -> Path:
    return _REPO_ROOT / _FIXTURE_REL


def load_baseline_workflow() -> dict[str, dict[str, Any]]:
    path = _fixture_path()
    with path.open(encoding="utf-8") as handle:
        data = json.load(handle)
    if not isinstance(data, dict):
        raise TypeError(f"expected object in {path}")
    return data


BASELINE: dict[str, dict[str, Any]] = load_baseline_workflow()

CASES: dict[str, dict[str, Any]] = {
    "control": {},
    "cfg5": {"cfg": 5.0},
    "steps20": {"steps": 20, "split": 10},
    "unipc": {"scheduler": "unipc", "shift": 8.0},
    "strength07": {"vace_strength": 0.7},
    "vace_end08": {"vace_end_percent": 0.8},
    "div32": {"divisible_by": 32},
    "sdpa": {"attention_mode": "sdpa"},
    "noquant": {"quantization": "disabled"},
    "combo": {
        "cfg": 5.0,
        "steps": 20,
        "split": 10,
        "scheduler": "unipc",
        "shift": 8.0,
    },
}

CASE_DESCRIPTIONS: dict[str, str] = {
    "control": "Recovered defective prompt (no overrides).",
    "cfg5": "CFG 5.0 on both samplers.",
    "steps20": "20 steps with high/low split at 10.",
    "unipc": "UniPC scheduler and shift 8.0 on both samplers.",
    "strength07": "Reduced VACE strength (0.7).",
    "vace_end08": "VACE active for first 80% of steps only.",
    "div32": "Resize divisible_by 32 instead of 16.",
    "sdpa": "SDPA attention on both model loaders.",
    "noquant": "Quantization disabled on both loaders.",
    "combo": "CFG 5.0 + 20 steps / split 10 + UniPC + shift 8.0.",
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
    seconds: float | None = None
    exec_span: float | None = None
    execution_cached: list[str] = field(default_factory=list)
    error: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def parse_execution_metrics(history: dict[str, Any]) -> tuple[float | None, list[str]]:
    """
    Return (exec_span_seconds, execution_cached_node_ids) from ComfyUI history.

    exec_span is execution_success.timestamp - execution_start.timestamp (ms → s).
    Wall-clock queue wait is excluded. Returns (None, cached) when timestamps are missing.
    """
    status = history.get("status") or {}
    messages = status.get("messages") or []

    start_ms: int | None = None
    end_ms: int | None = None
    cached: list[str] = []

    for entry in messages:
        if not isinstance(entry, (list, tuple)) or len(entry) < 2:
            continue
        kind = entry[0]
        payload = entry[1]
        if kind == "execution_start" and isinstance(payload, dict):
            ts = payload.get("timestamp")
            if isinstance(ts, (int, float)):
                start_ms = int(ts)
        elif kind == "execution_success" and isinstance(payload, dict):
            ts = payload.get("timestamp")
            if isinstance(ts, (int, float)):
                end_ms = int(ts)
        elif kind == "execution_cached" and isinstance(payload, list):
            cached = [str(x) for x in payload]

    if start_ms is None or end_ms is None or end_ms < start_ms:
        return None, cached
    return (end_ms - start_ms) / 1000.0, cached


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

    graph: dict[str, set[str]] = {nid: set() for nid in prompt}
    for nid, node in prompt.items():
        for ref_id, _ in iter_node_refs(node.get("inputs", {})):
            if ref_id in graph:
                graph[nid].add(ref_id)

    visiting: set[str] = set()
    visited: set[str] = set()

    def dfs(nid: str, stack: list[str]) -> None:
        if nid in visiting:
            issues.append(GraphIssue("cycle", " -> ".join(stack + [nid])))
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
        out[NODE_VACE]["inputs"]["vace_end_percent"] = float(
            overrides["vace_end_percent"]
        )

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

    if "prefix" in overrides:
        out[NODE_SAVE]["inputs"]["filename_prefix"] = str(overrides["prefix"])

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
    return _analyze_video(path, fps=fps)


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
        wall_start = time.time()
        prompt_id = runner.queue_prompt(prompt)
        result.prompt_id = prompt_id
        history = runner.wait_history(prompt_id)
        result.seconds = time.time() - wall_start
        exec_span, cached = parse_execution_metrics(history)
        result.exec_span = exec_span
        result.execution_cached = cached
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
