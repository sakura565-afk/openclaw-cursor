"""CLI кластера: health, route, run, parallel, furniture."""

from __future__ import annotations

import argparse
import json
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

from . import furniture, health, router
from .peers import resolve_peer
from .runner import RunResult, run_task


def _print_json(data: object) -> None:
    sys.stdout.write(json.dumps(data, ensure_ascii=False, indent=2) + "\n")


def cmd_health(_: argparse.Namespace) -> int:
    _print_json(health.check_all())
    return 0


def cmd_route(args: argparse.Namespace) -> int:
    machine, reason = router.route(args.profile)
    _print_json({"profile": args.profile, "machine": machine, "reason": reason})
    return 0


def cmd_run(args: argparse.Namespace) -> int:
    peer = resolve_peer(args.peer)
    result = run_task(
        peer,
        args.prompt,
        wait=not args.no_wait,
        timeout=args.timeout,
        poll=args.poll,
    )
    _print_json(
        {
            "peer": result.peer,
            "run_id": result.run_id,
            "status": result.status,
            "output": result.output,
            "elapsed_s": round(result.elapsed_s, 3),
            "error": result.error,
        }
    )
    return 0 if result.error is None and result.status not in {"failed", "error", "timeout"} else 1


def cmd_parallel(args: argparse.Namespace) -> int:
    jobs: list[tuple[str, str]] = []
    if args.home_prompt:
        jobs.append(("home", args.home_prompt))
    if args.work_prompt:
        jobs.append(("work", args.work_prompt))
    if not jobs:
        sys.stderr.write("укажите --home-prompt и/или --work-prompt\n")
        return 2

    results: dict[str, dict[str, object]] = {}

    def _one(name: str, prompt: str) -> RunResult:
        return run_task(resolve_peer(name), prompt, wait=True, timeout=args.timeout, poll=args.poll)

    with ThreadPoolExecutor(max_workers=len(jobs)) as pool:
        futs = {pool.submit(_one, name, prompt): name for name, prompt in jobs}
        for fut in as_completed(futs):
            name = futs[fut]
            res = fut.result()
            results[name] = {
                "run_id": res.run_id,
                "status": res.status,
                "output": res.output,
                "elapsed_s": round(res.elapsed_s, 3),
                "error": res.error,
            }

    _print_json(results)
    bad = any(r.get("status") in {"failed", "error", "timeout"} or r.get("error") for r in results.values())
    return 1 if bad else 0


def cmd_furniture(args: argparse.Namespace) -> int:
    path = Path(args.task_file)
    if not path.is_file():
        sys.stderr.write(f"файл не найден: {path}\n")
        return 2
    report = furniture.run_pipeline(path, stop_on_error=not args.continue_on_error)
    _print_json(report)
    return 0 if report.get("status") == "ok" else 1


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="scripts.cluster.cli",
        description="Оркестратор Hermes peer: work (M1) + home (M2).",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("health", help="Сводка GPU/ComfyUI/Ollama и удалённых peer")

    p_route = sub.add_parser("route", help="Маршрут профиля навыков")
    p_route.add_argument("--profile", required=True, help="gpu-heavy, backoffice, …")

    p_run = sub.add_parser("run", help="Одна задача на peer")
    p_run.add_argument("--peer", required=True, help="work | home | homepeer")
    p_run.add_argument("--prompt", required=True)
    p_run.add_argument("--no-wait", action="store_true", help="только POST /v1/runs")
    p_run.add_argument("--timeout", type=float, default=600)
    p_run.add_argument("--poll", type=float, default=4)

    p_par = sub.add_parser("parallel", help="Параллельно home и work")
    p_par.add_argument("--home-prompt", default="")
    p_par.add_argument("--work-prompt", default="")
    p_par.add_argument("--timeout", type=float, default=600)

    p_furn = sub.add_parser("furniture", help="Конвейер мебели из JSON")
    p_furn.add_argument("--task-file", required=True)
    p_furn.add_argument(
        "--continue-on-error",
        action="store_true",
        help="не останавливаться после сбоя этапа",
    )

    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    handlers = {
        "health": cmd_health,
        "route": cmd_route,
        "run": cmd_run,
        "parallel": cmd_parallel,
        "furniture": cmd_furniture,
    }
    return handlers[args.command](args)


if __name__ == "__main__":
    raise SystemExit(main())