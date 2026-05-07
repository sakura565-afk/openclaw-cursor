"""
Cursor Cloud Agents Batch Queue Manager
Automatically launches and monitors Cursor agents in batches.
"""
import json
import os
import re
import subprocess
import time
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

API_KEY = "crsr_8451e2fa232a5ca1982fd0876a5b5eb0632e24bd4e34680142b4a9c26be8c685"
REPO = "https://github.com/sakura565-afk/openclaw-cursor"
REF = "main"
BATCH_SIZE = 5

# Polling: exponential backoff between queue rounds while agents stay active
POLL_INTERVAL_INITIAL = 8.0
POLL_INTERVAL_MAX = 180.0
POLL_BACKOFF_MULTIPLIER = 1.6

# Retries for a single poll subprocess (transient CLI/network issues)
POLL_SUBPROCESS_MAX_ATTEMPTS = 5
POLL_SUBPROCESS_BASE_DELAY = 2.0
POLL_SUBPROCESS_MAX_DELAY = 45.0

# Merge retries (gh API / lock contention)
MERGE_MAX_ATTEMPTS = 4
MERGE_BASE_DELAY = 3.0

_LOG_ENV = os.environ.get("OPENCLAW_LOG_DIR", "").strip()
if _LOG_ENV:
    LOG_PATH = Path(_LOG_ENV).expanduser()
else:
    LOG_PATH = Path.home() / ".openclaw" / "workspace" / "memory"

# Workspace root (repo containing `scripts/`)
_REPO_ROOT = Path(__file__).resolve().parent.parent


def _cursor_cli_cwd() -> str:
    return str(_REPO_ROOT)


@dataclass
class PollResult:
    """Outcome of one poll invocation (after optional retries)."""

    status: str  # FINISHED | RUNNING | PROCESSING | FAILED | NOT_FOUND | ERROR
    stdout: str = ""
    stderr: str = ""
    combined: str = ""
    pr_urls: List[str] = field(default_factory=list)
    raw_json: Optional[Dict[str, Any]] = None
    error_message: Optional[str] = None


def get_active_agents() -> int:
    """Count currently running agents via API."""
    try:
        subprocess.run(
            ["python", "cursor_cloud_agent.py", "status"],
            capture_output=True,
            text=True,
            timeout=10,
            cwd=_cursor_cli_cwd(),
        )
        return 0
    except OSError:
        return 0


def launch_agent(branch: str, description: str) -> Dict:
    """Launch a single Cursor agent."""
    prompt = (
        f"Merge the branch origin/cursor/{branch} into main. Review code, "
        f"ensure tests pass, merge via PR. Reply with PR URL."
    )

    try:
        result = subprocess.run(
            [
                "python",
                "cursor_cloud_agent.py",
                "launch",
                "--api-key",
                API_KEY,
                "--repo",
                REPO,
                "--ref",
                REF,
                "--prompt",
                prompt,
                "--auto-pr",
            ],
            capture_output=True,
            text=True,
            timeout=30,
            cwd=_cursor_cli_cwd(),
        )

        output = result.stdout + result.stderr

        if "Agent launched:" in output:
            agent_id = output.split("Agent launched:")[1].split("|")[0].strip()
            return {"agent_id": agent_id, "branch": branch, "status": "LAUNCHED"}
        if "limit" in output.lower():
            return {"agent_id": None, "branch": branch, "status": "LIMIT_REACHED"}
        return {"agent_id": None, "branch": branch, "status": "FAILED"}

    except (OSError, subprocess.TimeoutExpired) as e:
        return {"agent_id": None, "branch": branch, "status": f"ERROR: {e}"}


def _extract_github_pr_urls(text: str) -> List[str]:
    seen = set()
    out: List[str] = []
    for m in re.finditer(
        r"https://github\.com/[^/\s\"']+/[^/\s\"']+/pull/\d+", text
    ):
        url = m.group(0).rstrip(").,]}\"'")
        if url not in seen:
            seen.add(url)
            out.append(url)
    return out


def _try_parse_json_object(text: str) -> Optional[Dict[str, Any]]:
    text = text.strip()
    if not text:
        return None
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass
    for start in (text.find("{"), text.rfind("{")):
        if start < 0:
            continue
        depth = 0
        for i in range(start, len(text)):
            if text[i] == "{":
                depth += 1
            elif text[i] == "}":
                depth -= 1
                if depth == 0:
                    chunk = text[start : i + 1]
                    try:
                        return json.loads(chunk)
                    except json.JSONDecodeError:
                        break
    return None


def _status_from_parsed(data: Dict[str, Any]) -> Optional[str]:
    for key in ("status", "state", "agentStatus"):
        val = data.get(key)
        if isinstance(val, str) and val.strip():
            return val.strip().upper()
    return None


def _pr_urls_from_parsed(data: Dict[str, Any]) -> List[str]:
    urls: List[str] = []
    candidates = [
        data.get("pullRequestUrl"),
        data.get("pr_url"),
        data.get("prUrl"),
        data.get("url"),
    ]
    nested = data.get("pullRequest") or data.get("pull_request")
    if isinstance(nested, dict):
        candidates.append(nested.get("url"))
        candidates.append(nested.get("html_url"))
    for c in candidates:
        if isinstance(c, str) and "github.com" in c and "/pull/" in c:
            urls.extend(_extract_github_pr_urls(c))
    return list(dict.fromkeys(urls))


def poll_agent_once(agent_id: str) -> PollResult:
    """Single poll attempt (no retry). Maps CLI output to PollResult."""
    try:
        result = subprocess.run(
            [
                "python",
                "cursor_cloud_agent.py",
                "poll",
                "--api-key",
                API_KEY,
                "--agent-id",
                agent_id,
            ],
            capture_output=True,
            text=True,
            timeout=60,
            cwd=_cursor_cli_cwd(),
        )
        out = result.stdout or ""
        err = result.stderr or ""
        combined = out + err

        data = _try_parse_json_object(combined)
        pr_urls = _extract_github_pr_urls(combined)
        status_from_json: Optional[str] = None
        if data:
            status_from_json = _status_from_parsed(data)
            pr_urls = list(dict.fromkeys(pr_urls + _pr_urls_from_parsed(data)))

        text_status = status_from_json
        if not text_status:
            if '"status": "FINISHED"' in combined or '"status":"FINISHED"' in combined:
                text_status = "FINISHED"
            elif '"status": "RUNNING"' in combined or '"status":"RUNNING"' in combined:
                text_status = "RUNNING"
            elif '"status": "FAILED"' in combined or '"status":"FAILED"' in combined:
                text_status = "FAILED"
            elif "404" in combined or "not found" in combined.lower():
                text_status = "NOT_FOUND"

        if text_status == "COMPLETED":
            text_status = "FINISHED"
        if text_status in (None, ""):
            if result.returncode != 0 and "404" in combined:
                text_status = "NOT_FOUND"
            elif result.returncode != 0:
                text_status = "ERROR"
            else:
                text_status = "PROCESSING"

        err_msg = None
        if text_status == "ERROR":
            err_msg = err.strip() or f"exit {result.returncode}"

        return PollResult(
            status=text_status,
            stdout=out,
            stderr=err,
            combined=combined,
            pr_urls=pr_urls,
            raw_json=data,
            error_message=err_msg,
        )

    except subprocess.TimeoutExpired as e:
        return PollResult(
            status="ERROR",
            error_message=str(e),
        )
    except OSError as e:
        return PollResult(status="ERROR", error_message=str(e))


def poll_agent_with_retry(agent_id: str) -> PollResult:
    """
    Poll agent status with exponential backoff between subprocess retries
    (transient failures only; terminal statuses return immediately).
    """
    delay = POLL_SUBPROCESS_BASE_DELAY
    last: Optional[PollResult] = None
    for attempt in range(POLL_SUBPROCESS_MAX_ATTEMPTS):
        last = poll_agent_once(agent_id)
        if last.status != "ERROR":
            return last
        if attempt < POLL_SUBPROCESS_MAX_ATTEMPTS - 1:
            time.sleep(min(delay, POLL_SUBPROCESS_MAX_DELAY))
            delay = min(delay * 2, POLL_SUBPROCESS_MAX_DELAY)
    assert last is not None
    return last


def _parse_github_pr_ref(pr_url: str) -> Optional[Tuple[str, str, str]]:
    m = re.match(
        r"https://github\.com/(?P<owner>[^/]+)/(?P<repo>[^/]+)/pull/(?P<num>\d+)",
        pr_url.strip(),
    )
    if not m:
        return None
    return m.group("owner"), m.group("repo"), m.group("num")


def merge_pull_request(pr_url: str) -> Tuple[bool, str]:
    """
    Auto-merge a GitHub PR using `gh` (merge commit).
    Retries with backoff on failure.
    """
    parsed = _parse_github_pr_ref(pr_url)
    if not parsed:
        return False, f"unrecognized PR URL: {pr_url}"

    owner, repo, num = parsed
    repo_spec = f"{owner}/{repo}"
    delay = MERGE_BASE_DELAY

    for attempt in range(MERGE_MAX_ATTEMPTS):
        try:
            proc = subprocess.run(
                [
                    "gh",
                    "pr",
                    "merge",
                    num,
                    "--repo",
                    repo_spec,
                    "--merge",
                ],
                capture_output=True,
                text=True,
                timeout=120,
                cwd=_cursor_cli_cwd(),
            )
            msg = (proc.stdout or "") + (proc.stderr or "")
            if proc.returncode == 0:
                return True, msg.strip() or "merged"
            err = msg.strip() or f"exit {proc.returncode}"
            if attempt < MERGE_MAX_ATTEMPTS - 1:
                time.sleep(delay)
                delay = min(delay * 2, 60.0)
                continue
            return False, err
        except (OSError, subprocess.TimeoutExpired) as e:
            err = str(e)
            if attempt < MERGE_MAX_ATTEMPTS - 1:
                time.sleep(delay)
                delay = min(delay * 2, 60.0)
                continue
            return False, err

    return False, "merge failed"


def log_queue_status(
    pending: List[str],
    running: Dict,
    completed: List[Dict],
    poll_interval: float,
):
    """Log queue status to file."""
    today = datetime.now().strftime("%Y-%m-%d")
    LOG_PATH.mkdir(parents=True, exist_ok=True)
    log_file = LOG_PATH / f"cursor_queue_{today}.md"

    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    content = f"# Cursor Queue Status — {timestamp}\n\n"
    content += f"Next poll backoff (cap {POLL_INTERVAL_MAX}s): **{poll_interval:.1f}s**\n\n"
    content += f"## Pending ({len(pending)})\n"
    for b in pending:
        content += f"- {b}\n"

    content += f"\n## Running ({len(running)})\n"
    for agent_id, branch in running.items():
        content += f"- {branch}: {agent_id}\n"

    content += "\n## Completed\n"
    for item in completed:
        extra = item.get("merge_status")
        line = (
            f"- {item['branch']}: {item['status']} → "
            f"{item.get('pr_url', 'no PR')}"
        )
        if extra:
            line += f" (merge: {extra})"
        content += line + "\n"

    log_file.write_text(content, encoding="utf-8")
    print(content)


def run_queue(branches: List[str], auto_merge: bool = True):
    """
    Main queue runner.
    Launches agents in batches, monitors with exponential backoff polling,
    retries transient poll errors, optionally merges PRs after success.
    """
    pending = branches.copy()
    running: Dict[str, str] = {}  # agent_id -> branch
    completed: List[Dict[str, Any]] = []

    print(f"🚀 Starting queue with {len(branches)} branches...")

    poll_interval = float(POLL_INTERVAL_INITIAL)

    while pending or running:
        # Launch next batch if we have capacity
        while len(running) < BATCH_SIZE and pending:
            branch = pending.pop(0)

            result = launch_agent(branch, "")

            if result["status"] == "LIMIT_REACHED":
                print(f"⚠️ Limit reached, {len(pending)} branches waiting")
                pending.insert(0, branch)
                break
            if result["agent_id"]:
                running[result["agent_id"]] = branch
                print(f"✅ Launched: {branch} ({result['agent_id']})")
                poll_interval = float(POLL_INTERVAL_INITIAL)
            else:
                print(f"❌ Failed to launch: {branch}")
                completed.append(
                    {
                        "branch": branch,
                        "status": "LAUNCH_FAILED",
                        "pr_url": None,
                        "merge_status": None,
                    }
                )

        completed_this_round = False

        for agent_id in list(running.keys()):
            result = poll_agent_with_retry(agent_id)

            if result.status == "ERROR":
                print(
                    f"⚠️ Poll error for {running.get(agent_id, '?')} "
                    f"({agent_id}): {result.error_message}"
                )
                continue

            if result.status == "FINISHED":
                branch = running.pop(agent_id)
                completed_this_round = True
                pr_url = result.pr_urls[0] if result.pr_urls else None

                merge_status = None
                if auto_merge and pr_url:
                    ok, msg = merge_pull_request(pr_url)
                    merge_status = "ok" if ok else f"failed: {msg}"
                    if ok:
                        print(f"🔀 Merged PR for {branch}: {pr_url}")
                    else:
                        print(f"⚠️ Merge failed for {branch}: {msg}")
                elif auto_merge and not pr_url:
                    merge_status = "skipped: no PR URL in poll output"
                    print(f"⚠️ Finished {branch} but no PR URL found to merge")

                completed.append(
                    {
                        "branch": branch,
                        "status": "FINISHED",
                        "pr_url": pr_url or f"https://cursor.com/agents/{agent_id}",
                        "merge_status": merge_status,
                    }
                )
                print(f"✅ Completed: {branch}")
                continue

            if result.status == "FAILED":
                branch = running.pop(agent_id)
                completed_this_round = True
                completed.append(
                    {
                        "branch": branch,
                        "status": "AGENT_FAILED",
                        "pr_url": result.pr_urls[0] if result.pr_urls else None,
                        "merge_status": None,
                    }
                )
                print(f"❌ Agent failed: {branch}")
                continue

            if result.status == "NOT_FOUND":
                branch = running.pop(agent_id, "unknown")
                completed_this_round = True
                completed.append(
                    {
                        "branch": branch,
                        "status": "NOT_FOUND",
                        "pr_url": None,
                        "merge_status": None,
                    }
                )
                print(f"⚠️ Agent not found (removed): {branch}")
                continue

            # RUNNING / PROCESSING: keep monitoring
        # Exponential backoff for idle polling: grow delay when nothing finished
        if running:
            if not completed_this_round:
                poll_interval = min(
                    poll_interval * POLL_BACKOFF_MULTIPLIER,
                    POLL_INTERVAL_MAX,
                )
            else:
                poll_interval = float(POLL_INTERVAL_INITIAL)

        log_queue_status(pending, running, completed, poll_interval)

        if running:
            time.sleep(poll_interval)
        elif pending:
            # Capacity may have opened; brief pause before retrying launch
            time.sleep(min(poll_interval, 5.0))

    print(f"\n🎉 Queue complete! {len(completed)} branches processed")
    return completed


def main():
    import argparse

    parser = argparse.ArgumentParser(description="Cursor Batch Queue Manager")
    parser.add_argument("action", choices=["run", "status"])
    parser.add_argument("--branches", nargs="+", help="Branch names to process")
    parser.add_argument(
        "--file", type=Path, help="File with branch list (one per line)"
    )
    parser.add_argument(
        "--no-merge",
        action="store_true",
        help="Do not run gh pr merge when an agent finishes successfully",
    )

    args = parser.parse_args()

    if args.action == "status":
        print("Queue status check not yet implemented")
        return

    branches = []
    if args.branches:
        branches = args.branches
    elif args.file and args.file.exists():
        branches = [
            l.strip() for l in args.file.read_text().splitlines() if l.strip()
        ]
    else:
        print("Error: specify --branches or --file")
        return

    run_queue(branches, auto_merge=not args.no_merge)


if __name__ == "__main__":
    main()
