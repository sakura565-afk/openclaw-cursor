#!/usr/bin/env python3
"""Multi-machine ComfyUI batch renderer.

Distributes API-format workflow prompts across a ``MachinePool`` (Local +
Work-PC by default), waits for each job, and downloads ``/view`` image outputs.

Typical usage::

    python -m scripts.comfy_multi_render render \\
        --workflow comfyui_workflows/flux_klein_face_swap.json \\
        --output-dir output/renders \\
        --count 4

    python -m scripts.comfy_multi_render render \\
        --workflow-dir jobs/ \\
        --output-dir output/renders \\
        --prefer Work-PC

Dry-run (no HTTP)::

    python -m scripts.comfy_multi_render render --workflow w.json --dry-run
"""

from __future__ import annotations

import argparse
import json
import ssl
import sys
import time
import uuid
import urllib.error
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence

from scripts.comfy_machine_pool import (
    DEFAULT_TIMEOUT_SEC,
    Machine,
    MachinePool,
    MachineStatus,
    probe_machine,
)


DEFAULT_LOG_PATH = Path("memory/comfy_multi_render_log.md")
DEFAULT_POLL_INTERVAL = 1.0
DEFAULT_JOB_TIMEOUT = 900.0
DEFAULT_RETRIES = 3


class RenderError(RuntimeError):
    """Batch render failure."""


@dataclass
class RenderJob:
    """One workflow submission."""

    job_id: str
    workflow: Dict[str, Any]
    label: str
    seed_override: Optional[int] = None

    def prompt_graph(self) -> Dict[str, Any]:
        graph = json.loads(json.dumps(self.workflow))  # deep copy
        if self.seed_override is not None:
            _apply_seed(graph, self.seed_override)
        return graph


@dataclass
class RenderResult:
    job_id: str
    label: str
    machine: str
    machine_url: str
    success: bool
    prompt_id: Optional[str] = None
    output_files: List[str] = field(default_factory=list)
    error: Optional[str] = None
    elapsed_sec: float = 0.0

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


class MarkdownLogger:
    def __init__(self, path: Path) -> None:
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)

    def log(self, title: str, message: str) -> None:
        ts = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
        with self.path.open("a", encoding="utf-8") as handle:
            handle.write(f"## {ts} | {title}\n\n{message}\n\n")


def _http_request(
    method: str,
    url: str,
    *,
    data: Optional[bytes] = None,
    headers: Optional[Dict[str, str]] = None,
    timeout: float = 60.0,
) -> bytes:
    req_headers = {"Accept": "application/json"}
    if headers:
        req_headers.update(headers)
    req = urllib.request.Request(url, data=data, headers=req_headers, method=method)
    ctx = ssl.create_default_context()
    with urllib.request.urlopen(req, timeout=timeout, context=ctx) as resp:
        return resp.read()


def _http_json(
    method: str,
    url: str,
    *,
    payload: Optional[Dict[str, Any]] = None,
    timeout: float = 60.0,
) -> Any:
    body = None
    headers: Dict[str, str] = {}
    if payload is not None:
        body = json.dumps(payload).encode("utf-8")
        headers["Content-Type"] = "application/json"
    raw = _http_request(method, url, data=body, headers=headers, timeout=timeout)
    if not raw:
        return {}
    return json.loads(raw.decode("utf-8"))


def _apply_seed(graph: Dict[str, Any], seed: int) -> None:
    """Set ``seed`` on common sampler / noise nodes when present."""
    for node in graph.values():
        if not isinstance(node, dict):
            continue
        inputs = node.get("inputs")
        if not isinstance(inputs, dict):
            continue
        if "seed" in inputs:
            inputs["seed"] = seed
        if "noise_seed" in inputs:
            inputs["noise_seed"] = seed


def _looks_like_api_graph(graph: Dict[str, Any]) -> bool:
    sample = next(iter(graph.values()), None)
    return isinstance(sample, dict) and "class_type" in sample


def load_workflow(path: Path) -> Dict[str, Any]:
    """Load a ComfyUI workflow JSON (API prompt format preferred)."""
    raw = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(raw, dict):
        raise RenderError(f"Workflow root must be an object: {path}")
    # Wrapped API export: {"prompt": {id: {class_type, inputs}}}
    prompt = raw.get("prompt")
    if isinstance(prompt, dict) and _looks_like_api_graph(prompt):
        return prompt
    # Direct API format: {"3": {"class_type": ..., "inputs": ...}}
    if _looks_like_api_graph(raw):
        return raw
    # UI format with "nodes" list — not executable via /prompt as-is
    if "nodes" in raw:
        raise RenderError(
            f"{path} looks like a UI-format workflow. "
            "Export API format (Save (API Format)) or wrap nodes yourself."
        )
    if isinstance(prompt, dict):
        return prompt
    raise RenderError(f"Unrecognized workflow format: {path}")


def collect_jobs(
    *,
    workflow: Optional[Path],
    workflow_dir: Optional[Path],
    count: int,
    seed: Optional[int],
) -> List[RenderJob]:
    paths: List[Path] = []
    if workflow is not None:
        paths.append(workflow)
    if workflow_dir is not None:
        paths.extend(sorted(workflow_dir.glob("*.json")))
    if not paths:
        raise RenderError("Provide --workflow and/or --workflow-dir with .json files.")

    jobs: List[RenderJob] = []
    for path in paths:
        graph = load_workflow(path)
        n = max(1, count)
        for i in range(n):
            job_seed = None if seed is None else seed + i
            jobs.append(
                RenderJob(
                    job_id=str(uuid.uuid4()),
                    workflow=graph,
                    label=f"{path.stem}" + (f"#{i + 1}" if n > 1 else ""),
                    seed_override=job_seed,
                )
            )
    return jobs


def _extract_images(history_entry: Dict[str, Any]) -> List[Dict[str, str]]:
    images: List[Dict[str, str]] = []
    outputs = history_entry.get("outputs") or {}
    if not isinstance(outputs, dict):
        return images
    for node_out in outputs.values():
        if not isinstance(node_out, dict):
            continue
        for key in ("images", "gifs"):
            items = node_out.get(key) or []
            if isinstance(items, list):
                for item in items:
                    if isinstance(item, dict) and item.get("filename"):
                        images.append(
                            {
                                "filename": str(item["filename"]),
                                "subfolder": str(item.get("subfolder") or ""),
                                "type": str(item.get("type") or "output"),
                            }
                        )
    return images


class ComfyRenderClient:
    """Minimal ComfyUI HTTP client for queue + download."""

    def __init__(self, base_url: str, *, timeout: float = 120.0, retries: int = DEFAULT_RETRIES) -> None:
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout
        self.retries = retries
        self.client_id = str(uuid.uuid4())

    def _with_retry(self, fn):
        last: Optional[BaseException] = None
        for attempt in range(1, self.retries + 1):
            try:
                return fn()
            except (urllib.error.URLError, urllib.error.HTTPError, TimeoutError, OSError) as exc:
                last = exc
                time.sleep(1.5 * attempt)
        raise RenderError(f"HTTP failed against {self.base_url}: {last}") from last

    def queue_prompt(self, prompt: Dict[str, Any]) -> str:
        def do():
            data = _http_json(
                "POST",
                f"{self.base_url}/prompt",
                payload={"prompt": prompt, "client_id": self.client_id},
                timeout=self.timeout,
            )
            prompt_id = data.get("prompt_id")
            if not prompt_id:
                raise RenderError(f"No prompt_id in response: {data}")
            return str(prompt_id)

        return self._with_retry(do)

    def wait_prompt(self, prompt_id: str, *, timeout_sec: float, poll: float) -> Dict[str, Any]:
        started = time.time()
        while time.time() - started < timeout_sec:
            history = self._with_retry(
                lambda: _http_json("GET", f"{self.base_url}/history/{prompt_id}", timeout=self.timeout)
            )
            if prompt_id in history:
                return history[prompt_id]
            time.sleep(poll)
        raise RenderError(f"Timed out waiting for prompt_id={prompt_id} on {self.base_url}")

    def download(self, image_ref: Dict[str, str], dest: Path) -> Path:
        params = urllib.parse.urlencode(
            {
                "filename": image_ref["filename"],
                "subfolder": image_ref.get("subfolder", ""),
                "type": image_ref.get("type", "output"),
            }
        )
        url = f"{self.base_url}/view?{params}"
        raw = self._with_retry(lambda: _http_request("GET", url, timeout=self.timeout))
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(raw)
        return dest


def select_machine(
    pool: MachinePool,
    *,
    prefer: Optional[str] = None,
    sticky: Optional[MachineStatus] = None,
) -> MachineStatus:
    if prefer:
        machine = pool.get(prefer)
        if machine is None:
            raise RenderError(f"Unknown preferred machine: {prefer}")
        status = probe_machine(machine, timeout=pool.timeout)
        if not status.online:
            raise RenderError(f"Preferred machine {prefer} is offline: {status.error}")
        return status
    if sticky is not None and sticky.online:
        refreshed = probe_machine(sticky.machine, timeout=pool.timeout)
        if refreshed.online:
            return refreshed
    chosen = pool.pick()
    if chosen is None:
        raise RenderError("No online ComfyUI machines in the pool.")
    return chosen


def run_one_job(
    job: RenderJob,
    machine: Machine,
    *,
    output_dir: Path,
    job_timeout: float,
    poll_interval: float,
    dry_run: bool,
    logger: MarkdownLogger,
) -> RenderResult:
    started = time.time()
    if dry_run:
        logger.log("Dry-run job", f"{job.label} → {machine.name} ({machine.normalized_url()})")
        return RenderResult(
            job_id=job.job_id,
            label=job.label,
            machine=machine.name,
            machine_url=machine.normalized_url(),
            success=True,
            prompt_id="dry-run",
            output_files=[],
            elapsed_sec=0.0,
        )

    client = ComfyRenderClient(machine.normalized_url())
    try:
        prompt_id = client.queue_prompt(job.prompt_graph())
        history = client.wait_prompt(prompt_id, timeout_sec=job_timeout, poll=poll_interval)
        images = _extract_images(history)
        saved: List[str] = []
        for idx, ref in enumerate(images):
            suffix = Path(ref["filename"]).suffix or ".png"
            out_name = f"{job.label}_{machine.name}_{idx}{suffix}".replace(" ", "_")
            path = client.download(ref, output_dir / out_name)
            saved.append(str(path))
        elapsed = time.time() - started
        logger.log(
            "Job done",
            f"{job.label} on {machine.name} prompt_id={prompt_id} files={saved} ({elapsed:.1f}s)",
        )
        return RenderResult(
            job_id=job.job_id,
            label=job.label,
            machine=machine.name,
            machine_url=machine.normalized_url(),
            success=True,
            prompt_id=prompt_id,
            output_files=saved,
            elapsed_sec=elapsed,
        )
    except Exception as exc:  # noqa: BLE001
        elapsed = time.time() - started
        logger.log("Job error", f"{job.label} on {machine.name}: {exc}")
        return RenderResult(
            job_id=job.job_id,
            label=job.label,
            machine=machine.name,
            machine_url=machine.normalized_url(),
            success=False,
            error=str(exc),
            elapsed_sec=elapsed,
        )


def dispatch_jobs(
    jobs: Sequence[RenderJob],
    *,
    pool: MachinePool,
    output_dir: Path,
    prefer: Optional[str] = None,
    parallel: int = 2,
    job_timeout: float = DEFAULT_JOB_TIMEOUT,
    poll_interval: float = DEFAULT_POLL_INTERVAL,
    dry_run: bool = False,
    logger: Optional[MarkdownLogger] = None,
) -> List[RenderResult]:
    """Fan out jobs across the pool (re-picking least-busy per job)."""
    log = logger or MarkdownLogger(DEFAULT_LOG_PATH)
    output_dir.mkdir(parents=True, exist_ok=True)
    results: List[RenderResult] = []
    workers = max(1, min(parallel, len(jobs) or 1))

    def _submit(job: RenderJob) -> RenderResult:
        status = select_machine(pool, prefer=prefer)
        return run_one_job(
            job,
            status.machine,
            output_dir=output_dir,
            job_timeout=job_timeout,
            poll_interval=poll_interval,
            dry_run=dry_run,
            logger=log,
        )

    log.log(
        "Batch start",
        f"jobs={len(jobs)} parallel={workers} prefer={prefer or 'auto'} dry_run={dry_run}",
    )

    if workers == 1 or len(jobs) <= 1:
        for job in jobs:
            results.append(_submit(job))
    else:
        with ThreadPoolExecutor(max_workers=workers) as executor:
            futures = {executor.submit(_submit, job): job for job in jobs}
            for fut in as_completed(futures):
                results.append(fut.result())

    # Stable order by original job list
    by_id = {r.job_id: r for r in results}
    ordered = [by_id[j.job_id] for j in jobs if j.job_id in by_id]
    ok = sum(1 for r in ordered if r.success)
    log.log("Batch done", f"success={ok}/{len(ordered)}")
    return ordered


def parse_args(argv: Optional[Sequence[str]] = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Distribute ComfyUI renders across machines.")
    parser.add_argument("--timeout", type=float, default=DEFAULT_TIMEOUT_SEC, help="Pool probe timeout.")
    sub = parser.add_subparsers(dest="command", required=True)

    render = sub.add_parser("render", help="Submit workflow(s) to the machine pool.")
    render.add_argument("--workflow", type=Path, default=None, help="Single API-format workflow JSON.")
    render.add_argument("--workflow-dir", type=Path, default=None, help="Directory of workflow JSON files.")
    render.add_argument("--output-dir", type=Path, default=Path("output/comfy_multi_render"))
    render.add_argument("--count", type=int, default=1, help="Copies per workflow (varies seed when --seed set).")
    render.add_argument("--seed", type=int, default=None, help="Base seed; incremented per copy.")
    render.add_argument("--prefer", default=None, help="Force machine name (Local / Work-PC).")
    render.add_argument("--parallel", type=int, default=2, help="Concurrent submissions.")
    render.add_argument("--job-timeout", type=float, default=DEFAULT_JOB_TIMEOUT)
    render.add_argument("--poll-interval", type=float, default=DEFAULT_POLL_INTERVAL)
    render.add_argument("--dry-run", action="store_true", help="Plan jobs without calling ComfyUI.")
    render.add_argument("--log", type=Path, default=DEFAULT_LOG_PATH)
    render.add_argument("--json-out", type=Path, default=None, help="Write results JSON path.")

    status = sub.add_parser("status", help="Show machine pool status (delegates to pool).")
    status.add_argument("--json", action="store_true")

    return parser.parse_args(argv)


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = parse_args(argv)
    pool = MachinePool(timeout=args.timeout)

    if args.command == "status":
        rows = [s.to_dict() for s in pool.probe_all()]
        if args.json:
            print(json.dumps(rows, indent=2))
        else:
            print(pool.status_table())
        return 0 if any(r["online"] for r in rows) else 1

    if args.command == "render":
        try:
            jobs = collect_jobs(
                workflow=args.workflow,
                workflow_dir=args.workflow_dir,
                count=args.count,
                seed=args.seed,
            )
        except RenderError as exc:
            print(f"[ERROR] {exc}", file=sys.stderr)
            return 2

        if args.dry_run:
            # Still allow dry-run without live machines
            results = dispatch_jobs(
                jobs,
                pool=pool,
                output_dir=args.output_dir,
                prefer=args.prefer,
                parallel=args.parallel,
                job_timeout=args.job_timeout,
                poll_interval=args.poll_interval,
                dry_run=True,
                logger=MarkdownLogger(args.log),
            )
        else:
            try:
                results = dispatch_jobs(
                    jobs,
                    pool=pool,
                    output_dir=args.output_dir,
                    prefer=args.prefer,
                    parallel=args.parallel,
                    job_timeout=args.job_timeout,
                    poll_interval=args.poll_interval,
                    dry_run=False,
                    logger=MarkdownLogger(args.log),
                )
            except RenderError as exc:
                print(f"[ERROR] {exc}", file=sys.stderr)
                return 1

        payload = [r.to_dict() for r in results]
        if args.json_out:
            args.json_out.parent.mkdir(parents=True, exist_ok=True)
            args.json_out.write_text(json.dumps(payload, indent=2), encoding="utf-8")
            print(f"[OK] Wrote {args.json_out}")

        ok = sum(1 for r in results if r.success)
        for r in results:
            mark = "OK" if r.success else "FAIL"
            detail = ", ".join(r.output_files) if r.success else (r.error or "error")
            print(f"[{mark}] {r.label} @ {r.machine}: {detail}")
        print(f"[DONE] {ok}/{len(results)} succeeded")
        return 0 if ok == len(results) else 1

    print(f"Unknown command: {args.command}", file=sys.stderr)
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
