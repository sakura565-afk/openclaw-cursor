"""Batch verified downloads with persistent job state."""

from __future__ import annotations

import argparse
import json
import os
import sys
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Any

from scripts.dl_verified import DownloadVerificationError, build_headers, build_url, download


def _load_json(path: str) -> Any:
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def _save_json(path: str, data: Any) -> None:
    directory = os.path.dirname(path)
    if directory:
        os.makedirs(directory, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, sort_keys=True)
        f.write("\n")


def _job_record(
    status: str,
    *,
    bytes_: int | None = None,
    sha256: str | None = None,
    attempts: int | None = None,
    error: str | None = None,
) -> dict[str, Any]:
    rec: dict[str, Any] = {"status": status}
    if bytes_ is not None:
        rec["bytes"] = bytes_
    if sha256 is not None:
        rec["sha256"] = sha256
    if attempts is not None:
        rec["attempts"] = attempts
    if error is not None:
        rec["error"] = error
    return rec


def run_jobs(
    jobs: list[dict[str, Any]],
    workers: int = 4,
    state_path: str | None = None,
    kind: str = "url",
    token: str | None = None,
    dry_run: bool = False,
    **download_kwargs: Any,
) -> dict[str, int]:
    """
    Run download jobs concurrently. Returns counts: ok, failed, skipped.
    """
    state: dict[str, Any] = {}
    if state_path and os.path.isfile(state_path):
        state = _load_json(state_path)

    lock = threading.Lock()
    counts = {"ok": 0, "failed": 0, "skipped": 0}

    def persist() -> None:
        if state_path:
            with lock:
                _save_json(state_path, state)

    pending: list[dict[str, Any]] = []
    for job in jobs:
        jid = str(job["id"])
        dest = job["dest"]
        with lock:
            rec = state.get(jid, {})
        if rec.get("status") == "done" and os.path.isfile(dest):
            recorded = rec.get("bytes")
            if recorded is not None and os.path.getsize(dest) == int(recorded):
                counts["skipped"] += 1
                continue
        pending.append(job)

    if dry_run:
        print(f"dry-run: would run {len(pending)} job(s), skip {counts['skipped']}")
        for job in pending:
            print(f"  - {job['id']}: {job['url']} -> {job['dest']}")
        return counts

    hdrs = build_headers(kind=kind, token=token)

    def run_one(job: dict[str, Any]) -> tuple[str, bool, str | None]:
        jid = str(job["id"])
        url = build_url(job["url"], kind=kind, token=token)
        dest = job["dest"]
        expect_size = job.get("expect_size")
        sha = job.get("sha256")
        try:
            result = download(
                url,
                dest,
                expected_size=expect_size,
                sha256=sha,
                headers=hdrs,
                **download_kwargs,
            )
            with lock:
                state[jid] = _job_record(
                    "done",
                    bytes_=result["bytes"],
                    sha256=result["sha256"],
                    attempts=result["attempts"],
                )
            persist()
            return jid, True, None
        except DownloadVerificationError as exc:
            with lock:
                state[jid] = _job_record("failed", error=str(exc))
            persist()
            return jid, False, str(exc)
        except Exception as exc:  # noqa: BLE001 — isolate job failures
            with lock:
                state[jid] = _job_record("failed", error=str(exc))
            persist()
            return jid, False, str(exc)

    with ThreadPoolExecutor(max_workers=max(1, workers)) as pool:
        futures = [pool.submit(run_one, job) for job in pending]
        for fut in as_completed(futures):
            jid, ok, err = fut.result()
            if ok:
                counts["ok"] += 1
                print(f"OK   {jid}")
            else:
                counts["failed"] += 1
                print(f"FAIL {jid}: {err}", file=sys.stderr)

    return counts


def _main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run verified download jobs from JSON")
    parser.add_argument("--jobs", required=True, help="Path to jobs JSON file (list of objects)")
    parser.add_argument("--state", help="Persistent state JSON path")
    parser.add_argument("--workers", type=int, default=4, help="Concurrent workers")
    parser.add_argument("--dry-run", action="store_true", help="List jobs without downloading")
    parser.add_argument(
        "--kind",
        choices=("url", "hf", "civitai"),
        default="url",
        help="URL kind for auth headers",
    )
    parser.add_argument("--token", help="API token (overrides env for hf/civitai)")
    args = parser.parse_args(argv)

    jobs = _load_json(args.jobs)
    if not isinstance(jobs, list):
        print("jobs file must contain a JSON list", file=sys.stderr)
        return 1

    counts = run_jobs(
        jobs,
        workers=args.workers,
        state_path=args.state,
        kind=args.kind,
        token=args.token,
        dry_run=args.dry_run,
    )

    print(
        f"Summary: ok={counts['ok']} failed={counts['failed']} skipped={counts['skipped']}"
    )
    return 1 if counts["failed"] else 0


if __name__ == "__main__":
    raise SystemExit(_main())
