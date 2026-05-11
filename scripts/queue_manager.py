"""
Cursor Cloud Agents Batch Queue Manager
Automatically launches and monitors Cursor agents in batches.
"""
import base64
import json
import os
import random
import re
import signal
import ssl
import subprocess
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

API_KEY = os.environ.get(
    "CURSOR_API_KEY",
    "crsr_8451e2fa232a5ca1982fd0876a5b5eb0632e24bd4e34680142b4a9c26be8c685",
)
REPO = "https://github.com/sakura565-afk/openclaw-cursor"
REF = "main"
BATCH_SIZE = 5

# Polling: exponential backoff between queue rounds while agents stay active
POLL_INTERVAL_INITIAL = 10.0
POLL_INTERVAL_MAX = 300.0
POLL_BACKOFF_MULTIPLIER = 2.0
POLL_JITTER_FRACTION = 0.20

# Transient HTTP / network retries before treating a poll as failed
TRANSIENT_MAX_ATTEMPTS = 3
TRANSIENT_BASE_DELAY = 1.5

# Stale agent detection: no remote status update for this long while still active
STALE_AFTER_SEC = 600.0

# Merge retries (gh API / lock contention)
MERGE_MAX_ATTEMPTS = 4
MERGE_BASE_DELAY = 3.0

CURSOR_API_BASE = os.environ.get("CURSOR_API_BASE", "https://api.cursor.com").rstrip(
    "/"
)

_LOG_ENV = os.environ.get("OPENCLAW_LOG_DIR", "").strip()
if _LOG_ENV:
    LOG_PATH = Path(_LOG_ENV).expanduser()
else:
    LOG_PATH = Path.home() / ".openclaw" / "workspace" / "memory"

# User-requested default; override on non-Windows hosts via OPENCLAW_MEMORY_DIR
_DEFAULT_MEMORY = Path(r"C:\Users\user\.openclaw\workspace\memory")
MEMORY_DIR = Path(
    os.environ.get("OPENCLAW_MEMORY_DIR", str(_DEFAULT_MEMORY))
).expanduser()
QUEUE_STATE_PATH = MEMORY_DIR / "queue_state.json"
QUEUE_METRICS_PATH = MEMORY_DIR / "queue_metrics.json"

# Workspace root (repo containing `scripts/`)
_REPO_ROOT = Path(__file__).resolve().parent.parent

_shutdown_lock = threading.Lock()
_shutdown_requested = False


def _set_shutdown_requested() -> None:
    global _shutdown_requested
    with _shutdown_lock:
        _shutdown_requested = True


def _is_shutdown_requested() -> bool:
    with _shutdown_lock:
        return _shutdown_requested


def _install_signal_handlers() -> None:
    def handler(_signum: int, _frame: Any) -> None:
        _set_shutdown_requested()
        print("\nShutdown requested; finishing current poll cycle…")

    signal.signal(signal.SIGINT, handler)
    if hasattr(signal, "SIGTERM"):
        signal.signal(signal.SIGTERM, handler)


def _cursor_cli_cwd() -> str:
    return str(_REPO_ROOT)


def _parse_iso8601(ts: Optional[str]) -> Optional[float]:
    if not ts or not isinstance(ts, str):
        return None
    s = ts.strip()
    if not s:
        return None
    try:
        if s.endswith("Z"):
            s = s[:-1] + "+00:00"
        dt = datetime.fromisoformat(s)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.timestamp()
    except ValueError:
        return None


def _sleep_with_jitter(interval: float) -> None:
    """Sleep interval seconds plus up to POLL_JITTER_FRACTION of interval."""
    extra = interval * random.uniform(0.0, POLL_JITTER_FRACTION)
    time.sleep(interval + extra)


def _is_transient_http_status(code: int) -> bool:
    return code == 429 or 500 <= code <= 599


def _is_transient_exception(exc: BaseException) -> bool:
    if isinstance(exc, subprocess.TimeoutExpired):
        return True
    if isinstance(exc, OSError) and getattr(exc, "errno", None) in (
        110,
        11,
        35,
        10060,
    ):  # ETIMEDOUT variants
        return True
    if isinstance(exc, ssl.SSLError):
        return True
    if isinstance(exc, urllib.error.URLError):
        r = exc.reason
        if isinstance(r, ssl.SSLError):
            return True
        if isinstance(r, (TimeoutError, OSError)):
            return True
        msg = str(r).lower()
        if "timed out" in msg or "timeout" in msg:
            return True
        if "ssl" in msg:
            return True
    return False


def _auth_header() -> str:
    raw = f"{API_KEY}:".encode("utf-8")
    return "Basic " + base64.b64encode(raw).decode("ascii")


def _cursor_api_json(method: str, path: str, timeout: float = 60.0) -> Tuple[Any, int]:
    """
    Return (parsed_json_or_None, http_status).
    Retries transient HTTP 5xx / 429 and network timeouts up to TRANSIENT_MAX_ATTEMPTS.
    """
    url = f"{CURSOR_API_BASE}{path}"
    delay = float(TRANSIENT_BASE_DELAY)
    op = f"{method} {path}"
    for attempt in range(TRANSIENT_MAX_ATTEMPTS):
        req = urllib.request.Request(
            url,
            method=method,
            headers={"Authorization": _auth_header(), "Accept": "application/json"},
        )
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                body = resp.read().decode("utf-8", errors="replace")
                code = resp.getcode() or 200
                if not body.strip():
                    return None, code
                try:
                    return json.loads(body), code
                except json.JSONDecodeError:
                    return {"_raw": body}, code
        except urllib.error.HTTPError as e:
            err_body = e.read().decode("utf-8", errors="replace") if e.fp else ""
            code = e.code
            if _is_transient_http_status(code) and attempt < TRANSIENT_MAX_ATTEMPTS - 1:
                print(
                    f"⚠️ {op}: HTTP {code}, retry {attempt + 1}/"
                    f"{TRANSIENT_MAX_ATTEMPTS - 1}…"
                )
                _sleep_with_jitter(delay)
                delay = min(delay * 2, 60.0)
                continue
            try:
                parsed = json.loads(err_body) if err_body.strip() else None
            except json.JSONDecodeError:
                parsed = {"_raw": err_body}
            return parsed if isinstance(parsed, dict) else {"_raw": err_body}, code
        except (urllib.error.URLError, ssl.SSLError, TimeoutError, OSError) as e:
            if _is_transient_exception(e) and attempt < TRANSIENT_MAX_ATTEMPTS - 1:
                print(
                    f"⚠️ {op}: {e!r}, retry {attempt + 1}/"
                    f"{TRANSIENT_MAX_ATTEMPTS - 1}…"
                )
                _sleep_with_jitter(delay)
                delay = min(delay * 2, 60.0)
                continue
            raise
    return None, 0


def _agents_index_from_list() -> Dict[str, Dict[str, Any]]:
    """Fetch /v1/agents (paginated) and return id -> list item."""
    out: Dict[str, Dict[str, Any]] = {}
    cursor_param: Optional[str] = None
    while True:
        q = "limit=100"
        if cursor_param:
            q += f"&cursor={urllib.parse.quote(cursor_param, safe='')}"
        data, code = _cursor_api_json("GET", f"/v1/agents?{q}")
        if code != 200 or not isinstance(data, dict):
            break
        items = data.get("items") or []
        if isinstance(items, list):
            for it in items:
                if isinstance(it, dict) and it.get("id"):
                    out[str(it["id"])] = it
        cursor_param = data.get("nextCursor")
        if not cursor_param:
            break
    return out


def _get_agent(agent_id: str) -> Tuple[Optional[Dict[str, Any]], int]:
    data, code = _cursor_api_json("GET", f"/v1/agents/{urllib.parse.quote(agent_id)}")
    if code == 200 and isinstance(data, dict):
        return data, code
    return data if isinstance(data, dict) else None, code


def _get_run(agent_id: str, run_id: str) -> Tuple[Optional[Dict[str, Any]], int]:
    path = (
        f"/v1/agents/{urllib.parse.quote(agent_id)}"
        f"/runs/{urllib.parse.quote(run_id)}"
    )
    data, code = _cursor_api_json("GET", path)
    if code == 200 and isinstance(data, dict):
        return data, code
    return data if isinstance(data, dict) else None, code


@dataclass
class PollResult:
    """Outcome of one poll invocation (after optional retries)."""

    status: str  # FINISHED | RUNNING | PROCESSING | FAILED | NOT_FOUND | ERROR | TIMEOUT
    stdout: str = ""
    stderr: str = ""
    combined: str = ""
    pr_urls: List[str] = field(default_factory=list)
    raw_json: Optional[Dict[str, Any]] = None
    error_message: Optional[str] = None
    remote_updated_ts: Optional[float] = None


def _run_status_to_poll_status(run_status: Optional[str]) -> str:
    if not run_status:
        return "PROCESSING"
    u = run_status.strip().upper()
    if u in ("FINISHED", "COMPLETED"):
        return "FINISHED"
    if u in ("FAILED", "CANCELLED", "STOPPED"):
        return "FAILED"
    if u in ("RUNNING", "CREATING"):
        return "RUNNING"
    return "PROCESSING"


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


def poll_agent_once_subprocess(agent_id: str) -> PollResult:
    """Fallback: single poll via CLI (no retry)."""
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


def _poll_subprocess_with_transient_retries(agent_id: str) -> PollResult:
    delay = TRANSIENT_BASE_DELAY
    last: Optional[PollResult] = None
    for attempt in range(TRANSIENT_MAX_ATTEMPTS):
        last = poll_agent_once_subprocess(agent_id)
        if last.status != "ERROR":
            return last
        msg = (last.error_message or "").lower()
        combined = (last.combined or "").lower()
        transient = (
            "timeout" in msg
            or "timed out" in msg
            or "ssl" in msg
            or "500" in combined
            or "502" in combined
            or "503" in combined
            or "504" in combined
        )
        if transient and attempt < TRANSIENT_MAX_ATTEMPTS - 1:
            _sleep_with_jitter(delay)
            delay = min(delay * 2, 60.0)
            continue
        return last
    assert last is not None
    return last


def _merge_poll_dicts(
    agent: Optional[Dict[str, Any]], run: Optional[Dict[str, Any]]
) -> Dict[str, Any]:
    merged: Dict[str, Any] = {}
    if isinstance(agent, dict):
        merged.update(agent)
    if isinstance(run, dict):
        merged.update(run)
    return merged


def poll_running_agents_batch(
    running: Dict[str, str],
    agent_tracker: Dict[str, Dict[str, Any]],
) -> Dict[str, PollResult]:
    """
    Poll all running agent IDs in one cycle using the Cursor HTTP API.
    Falls back to subprocess poll if the API is unavailable.
    """
    results: Dict[str, PollResult] = {}
    now = time.time()

    try:
        index = _agents_index_from_list()
    except Exception as e:
        print(f"⚠️ Batch agent list failed ({e}); falling back to CLI poll.")
        ts = time.time()
        for agent_id in running:
            tr = agent_tracker.setdefault(
                agent_id,
                {"poll_count": 0, "started_ts": ts, "last_poll_ts": 0.0},
            )
            tr["poll_count"] = int(tr.get("poll_count", 0)) + 1
            tr["last_poll_ts"] = ts
            results[agent_id] = _poll_subprocess_with_transient_retries(agent_id)
        return results

    def fetch_one(agent_id: str) -> Tuple[str, PollResult]:
        tr = agent_tracker.setdefault(
            agent_id,
            {"poll_count": 0, "started_ts": now, "last_poll_ts": 0.0},
        )
        tr["poll_count"] = int(tr.get("poll_count", 0)) + 1
        tr["last_poll_ts"] = now

        item = index.get(agent_id)
        agent_body: Optional[Dict[str, Any]] = None
        code = 200
        if not item:
            agent_body, code = _get_agent(agent_id)
            if code == 404:
                return agent_id, PollResult(
                    status="NOT_FOUND",
                    error_message="agent not found",
                )
            if agent_body is None:
                return agent_id, PollResult(
                    status="ERROR",
                    error_message=f"GET agent HTTP {code}",
                )
        else:
            agent_body = item

        assert agent_body is not None
        latest_run_id = agent_body.get("latestRunId")
        remote_ts = _parse_iso8601(agent_body.get("updatedAt"))

        if not latest_run_id:
            st = _status_from_parsed(agent_body) or "PROCESSING"
            if st in ("FINISHED", "FAILED", "STOPPED", "CANCELLED"):
                prs = _pr_urls_from_parsed(agent_body)
                return agent_id, PollResult(
                    status=_run_status_to_poll_status(st),
                    pr_urls=prs,
                    raw_json=agent_body,
                    remote_updated_ts=remote_ts,
                )
            poll_st = _run_status_to_poll_status(st)
            if (
                poll_st in ("RUNNING", "PROCESSING")
                and remote_ts is not None
                and now - remote_ts > STALE_AFTER_SEC
            ):
                return agent_id, PollResult(
                    status="TIMEOUT",
                    pr_urls=_pr_urls_from_parsed(agent_body),
                    raw_json=agent_body,
                    remote_updated_ts=remote_ts,
                    error_message="no remote status update within stale window",
                )
            tr["last_remote_updated_ts"] = remote_ts
            return agent_id, PollResult(
                status="PROCESSING",
                raw_json=agent_body,
                remote_updated_ts=remote_ts,
            )

        run_body, rcode = _get_run(agent_id, str(latest_run_id))
        if rcode == 404:
            return agent_id, PollResult(
                status="NOT_FOUND",
                error_message="run not found",
            )
        if run_body is None:
            return agent_id, PollResult(
                status="ERROR",
                error_message=f"GET run HTTP {rcode}",
            )

        merged_for_pr = _merge_poll_dicts(agent_body, run_body)
        run_status = run_body.get("status")
        prs = _pr_urls_from_parsed(merged_for_pr)
        prs = list(dict.fromkeys(prs + _extract_github_pr_urls(json.dumps(run_body))))
        poll_st = _run_status_to_poll_status(
            run_status if isinstance(run_status, str) else None
        )
        run_updated = _parse_iso8601(run_body.get("updatedAt"))
        effective_remote = run_updated or remote_ts
        if poll_st in ("RUNNING", "PROCESSING") and effective_remote is not None:
            if now - effective_remote > STALE_AFTER_SEC:
                return agent_id, PollResult(
                    status="TIMEOUT",
                    pr_urls=prs,
                    raw_json=merged_for_pr,
                    remote_updated_ts=effective_remote,
                    error_message="no remote status update within stale window",
                )

        tr["last_remote_updated_ts"] = effective_remote
        return agent_id, PollResult(
            status=poll_st,
            pr_urls=prs,
            raw_json=merged_for_pr,
            remote_updated_ts=effective_remote,
        )

    max_workers = min(12, max(1, len(running)))
    with ThreadPoolExecutor(max_workers=max_workers) as ex:
        futures = {ex.submit(fetch_one, aid): aid for aid in running}
        for fut in as_completed(futures):
            agent_id = futures[fut]
            try:
                aid, pr = fut.result()
                results[aid] = pr
            except urllib.error.HTTPError as e:
                if _is_transient_http_status(e.code):
                    results[agent_id] = _poll_subprocess_with_transient_retries(agent_id)
                else:
                    results[agent_id] = PollResult(
                        status="ERROR",
                        error_message=f"HTTP {e.code}",
                    )
            except (urllib.error.URLError, ssl.SSLError, TimeoutError, OSError) as e:
                if _is_transient_exception(e):
                    results[agent_id] = _poll_subprocess_with_transient_retries(
                        agent_id
                    )
                else:
                    results[agent_id] = PollResult(
                        status="ERROR",
                        error_message=str(e),
                    )
            except Exception as e:
                results[agent_id] = PollResult(status="ERROR", error_message=str(e))

    return results


def get_active_agents() -> int:
    """Count agents visible via API (best effort)."""
    try:
        idx = _agents_index_from_list()
        return len(idx)
    except Exception:
        return 0


def launch_agent_once(branch: str, description: str) -> Dict[str, Any]:
    """Launch a single Cursor agent (one attempt)."""
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


def launch_agent(branch: str, description: str) -> Dict[str, Any]:
    """Launch with transient retries (subprocess / network)."""
    delay = TRANSIENT_BASE_DELAY
    last: Optional[Dict[str, Any]] = None
    for attempt in range(TRANSIENT_MAX_ATTEMPTS):
        last = launch_agent_once(branch, description)
        status = last.get("status")
        if status == "LAUNCHED" or status == "LIMIT_REACHED":
            return last
        if status == "FAILED":
            return last
        transient = False
        if isinstance(status, str) and status.startswith("ERROR"):
            emsg = status.lower()
            transient = (
                "timeout" in emsg
                or "timed out" in emsg
                or "ssl" in emsg
                or "500" in emsg
            )
        if transient and attempt < TRANSIENT_MAX_ATTEMPTS - 1:
            _sleep_with_jitter(delay)
            delay = min(delay * 2, 60.0)
            continue
        return last
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


def _load_json_file(path: Path) -> Optional[Dict[str, Any]]:
    try:
        if path.exists():
            return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        pass
    return None


def load_queue_state() -> Optional[Dict[str, Any]]:
    return _load_json_file(QUEUE_STATE_PATH)


def save_queue_state(
    pending: List[str],
    running: Dict[str, str],
    completed: List[Dict[str, Any]],
    agent_tracker: Dict[str, Dict[str, Any]],
    poll_interval: float,
) -> None:
    MEMORY_DIR.mkdir(parents=True, exist_ok=True)
    payload = {
        "version": 1,
        "updated_at": datetime.now(timezone.utc).isoformat(),
        "pending": pending,
        "running": running,
        "completed": completed,
        "agent_tracker": agent_tracker,
        "poll_interval": poll_interval,
    }
    tmp = QUEUE_STATE_PATH.with_suffix(".tmp")
    tmp.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    tmp.replace(QUEUE_STATE_PATH)


def append_metrics_completion(
    branch: str,
    agent_id: Optional[str],
    time_to_complete_sec: Optional[float],
    success: bool,
    poll_count: int,
    terminal_status: str,
) -> None:
    MEMORY_DIR.mkdir(parents=True, exist_ok=True)
    existing = _load_json_file(QUEUE_METRICS_PATH) or {
        "agents": [],
        "aggregate": {},
    }
    agents: List[Dict[str, Any]] = list(existing.get("agents") or [])
    agents.append(
        {
            "branch": branch,
            "agent_id": agent_id,
            "time_to_complete_sec": time_to_complete_sec,
            "success": success,
            "poll_count": poll_count,
            "terminal_status": terminal_status,
            "recorded_at": datetime.now(timezone.utc).isoformat(),
        }
    )
    total = len(agents)
    successes = sum(1 for a in agents if a.get("success"))
    polls = [int(a.get("poll_count") or 0) for a in agents]
    ttc = [float(a["time_to_complete_sec"]) for a in agents if a.get("time_to_complete_sec") is not None]
    existing["agents"] = agents
    existing["aggregate"] = {
        "total_completed": total,
        "success_rate": (successes / total) if total else 0.0,
        "avg_poll_count": (sum(polls) / len(polls)) if polls else 0.0,
        "avg_time_to_complete_sec": (sum(ttc) / len(ttc)) if ttc else None,
        "updated_at": datetime.now(timezone.utc).isoformat(),
    }
    tmp = QUEUE_METRICS_PATH.with_suffix(".tmp")
    tmp.write_text(json.dumps(existing, indent=2), encoding="utf-8")
    tmp.replace(QUEUE_METRICS_PATH)


def log_queue_status(
    pending: List[str],
    running: Dict[str, str],
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


def run_queue(
    branches: List[str],
    auto_merge: bool = True,
    fresh: bool = False,
    resume: bool = True,
):
    """
    Main queue runner.
    Launches agents in batches, monitors with HTTP batch polling,
    exponential backoff + jitter, optional PR merge, state + metrics persistence.
    """
    _install_signal_handlers()

    if fresh and QUEUE_STATE_PATH.exists():
        QUEUE_STATE_PATH.unlink()

    pending: List[str] = []
    running: Dict[str, str] = {}
    completed: List[Dict[str, Any]] = []
    agent_tracker: Dict[str, Dict[str, Any]] = {}

    saved = None if fresh or not resume else load_queue_state()
    if (
        saved
        and (saved.get("pending") or saved.get("running"))
        and isinstance(saved.get("pending"), list)
        and isinstance(saved.get("running"), dict)
    ):
        pending = [str(x) for x in saved["pending"]]
        running = {str(k): str(v) for k, v in saved["running"].items()}
        completed = list(saved.get("completed") or [])
        agent_tracker = dict(saved.get("agent_tracker") or {})
        print(f"📂 Resumed queue state from {QUEUE_STATE_PATH}")

    if not pending and not running:
        pending = branches.copy()

    print(f"🚀 Starting queue with {len(pending) + len(running)} branches pending/running…")

    poll_interval = float(POLL_INTERVAL_INITIAL)

    def persist_state() -> None:
        save_queue_state(pending, running, completed, agent_tracker, poll_interval)

    while (pending or running) and not _is_shutdown_requested():
        # Launch next batch if we have capacity
        while len(running) < BATCH_SIZE and pending and not _is_shutdown_requested():
            branch = pending.pop(0)

            result = launch_agent(branch, "")

            if result["status"] == "LIMIT_REACHED":
                print(f"⚠️ Limit reached, {len(pending)} branches waiting")
                pending.insert(0, branch)
                break
            if result.get("agent_id"):
                aid = str(result["agent_id"])
                running[aid] = branch
                agent_tracker.setdefault(
                    aid,
                    {
                        "poll_count": 0,
                        "started_ts": time.time(),
                        "last_poll_ts": 0.0,
                    },
                )
                print(f"✅ Launched: {branch} ({aid})")
                poll_interval = float(POLL_INTERVAL_INITIAL)
            else:
                print(f"❌ Failed to launch: {branch}")
                completed.append(
                    {
                        "branch": branch,
                        "status": "LAUNCH_FAILED",
                        "pr_url": None,
                        "merge_status": None,
                        "agent_id": None,
                    }
                )
                append_metrics_completion(
                    branch,
                    None,
                    None,
                    success=False,
                    poll_count=0,
                    terminal_status="LAUNCH_FAILED",
                )

        completed_this_round = False
        batch: Dict[str, PollResult] = {}
        if running:
            batch = poll_running_agents_batch(running, agent_tracker)

        for agent_id in list(running.keys()):
            if not running:
                break
            if agent_id not in running:
                continue
            result = batch.get(agent_id)
            if result is None:
                continue

            if result.status == "ERROR":
                print(
                    f"⚠️ Poll error for {running.get(agent_id, '?')} "
                    f"({agent_id}): {result.error_message}"
                )
                branch = running.pop(agent_id, "unknown")
                completed_this_round = True
                completed.append(
                    {
                        "branch": branch,
                        "status": "POLL_FAILED",
                        "pr_url": None,
                        "merge_status": None,
                        "agent_id": agent_id,
                    }
                )
                tr = agent_tracker.pop(agent_id, {})
                append_metrics_completion(
                    branch,
                    agent_id,
                    time.time() - float(tr.get("started_ts", time.time())),
                    success=False,
                    poll_count=int(tr.get("poll_count", 0)),
                    terminal_status="POLL_FAILED",
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
                        "agent_id": agent_id,
                    }
                )
                tr = agent_tracker.pop(agent_id, {})
                append_metrics_completion(
                    branch,
                    agent_id,
                    time.time() - float(tr.get("started_ts", time.time())),
                    success=True,
                    poll_count=int(tr.get("poll_count", 0)),
                    terminal_status="FINISHED",
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
                        "agent_id": agent_id,
                    }
                )
                tr = agent_tracker.pop(agent_id, {})
                append_metrics_completion(
                    branch,
                    agent_id,
                    time.time() - float(tr.get("started_ts", time.time())),
                    success=False,
                    poll_count=int(tr.get("poll_count", 0)),
                    terminal_status="AGENT_FAILED",
                )
                print(f"❌ Agent failed: {branch}")
                continue

            if result.status == "TIMEOUT":
                branch = running.pop(agent_id, "unknown")
                completed_this_round = True
                completed.append(
                    {
                        "branch": branch,
                        "status": "TIMEOUT",
                        "pr_url": result.pr_urls[0] if result.pr_urls else None,
                        "merge_status": None,
                        "agent_id": agent_id,
                    }
                )
                tr = agent_tracker.pop(agent_id, {})
                append_metrics_completion(
                    branch,
                    agent_id,
                    time.time() - float(tr.get("started_ts", time.time())),
                    success=False,
                    poll_count=int(tr.get("poll_count", 0)),
                    terminal_status="TIMEOUT",
                )
                print(f"⏱️ Agent timed out (stale): {branch}")
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
                        "agent_id": agent_id,
                    }
                )
                tr = agent_tracker.pop(agent_id, {})
                append_metrics_completion(
                    branch,
                    agent_id,
                    time.time() - float(tr.get("started_ts", time.time())),
                    success=False,
                    poll_count=int(tr.get("poll_count", 0)),
                    terminal_status="NOT_FOUND",
                )
                print(f"⚠️ Agent not found (removed): {branch}")
                continue

            # RUNNING / PROCESSING: keep monitoring
        if running:
            if not completed_this_round:
                poll_interval = min(
                    poll_interval * POLL_BACKOFF_MULTIPLIER,
                    POLL_INTERVAL_MAX,
                )
            else:
                poll_interval = float(POLL_INTERVAL_INITIAL)

        log_queue_status(pending, running, completed, poll_interval)
        persist_state()

        if _is_shutdown_requested():
            break

        if running:
            _sleep_with_jitter(poll_interval)
        elif pending:
            _sleep_with_jitter(min(poll_interval, 5.0))

    persist_state()
    if _is_shutdown_requested():
        print("\n🛑 Shutdown complete; state saved.")
    else:
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
    parser.add_argument(
        "--fresh",
        action="store_true",
        help="Ignore saved queue_state.json and start from the given branch list",
    )

    args = parser.parse_args()

    if args.action == "status":
        n = get_active_agents()
        print(f"Agents visible via API (approximate): {n}")
        return

    branches: List[str] = []
    if args.branches:
        branches = args.branches
    elif args.file and args.file.exists():
        branches = [
            l.strip() for l in args.file.read_text().splitlines() if l.strip()
        ]
    elif not args.fresh:
        saved = load_queue_state()
        if saved and (saved.get("pending") or saved.get("running")):
            run_queue([], auto_merge=not args.no_merge, fresh=False, resume=True)
            return

    if not branches:
        print("Error: specify --branches or --file (or rely on saved queue state)")
        return

    explicit_branches = bool(args.branches) or (
        args.file is not None and args.file.exists()
    )
    run_queue(
        branches,
        auto_merge=not args.no_merge,
        fresh=args.fresh,
        resume=not explicit_branches,
    )


if __name__ == "__main__":
    main()
