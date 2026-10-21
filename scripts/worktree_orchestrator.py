#!/usr/bin/env python3
"""Per-agent ``git worktree`` isolation for parallel Cursor cloud agents.

Cloud agents used to share a single checkout, so one agent's uncommitted edits
were visible to — and could break — every other agent. This module hands each
agent its own worktree on a dedicated ``cursor/<slug>`` branch, tracks the
handouts in ``data/worktrees.json`` and can reap orphans left behind by a
crashed runner.

Stdlib only; every git call goes through ``subprocess.run([...], shell=False)``
with a 60 second timeout.

CLI::

    python -m scripts.worktree_orchestrator create --slug demo
    python -m scripts.worktree_orchestrator list
    python -m scripts.worktree_orchestrator release --slug demo --keep
    python -m scripts.worktree_orchestrator reap --older-than-hours 24
    python -m scripts.worktree_orchestrator doctor
"""

from __future__ import annotations

import argparse
import csv
import json
import logging
import os
import re
import subprocess
import sys
from dataclasses import dataclass, fields
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple

try:
    from scripts.windows_console import decode_console_output
except ImportError:  # pragma: no cover - direct script execution
    import sys as _sys
    from pathlib import Path as _Path
    _sys.path.insert(0, str(_Path(__file__).resolve().parent))
    from windows_console import decode_console_output

LOGGER = logging.getLogger("worktree_orchestrator")

GIT_TIMEOUT = 60
BRANCH_PREFIX = "cursor/"
DEFAULT_WORKTREE_DIRNAME = ".worktrees"
DEFAULT_REGISTRY_RELPATH = Path("data") / "worktrees.json"
REGISTRY_VERSION = 1

STATE_ACTIVE = "active"
STATE_RELEASED = "released"
STATE_KEPT = "kept"
STATE_ERROR = "error"
STATE_UNREGISTERED = "unregistered"

# States whose worktree is still expected to exist on disk.
LIVE_STATES = (STATE_ACTIVE, STATE_KEPT, STATE_ERROR)

_SLUG_INVALID_RE = re.compile(r"[^A-Za-z0-9._-]+")


class WorktreeError(RuntimeError):
    """Raised when a git operation required by the orchestrator fails."""


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _isoformat(value: datetime) -> str:
    return value.isoformat()


def _parse_iso(value: Optional[str]) -> Optional[datetime]:
    if not value or not isinstance(value, str):
        return None
    text = value.strip()
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed


def _pid_alive(pid: Optional[int]) -> bool:
    """Best-effort liveness check for the process that owns a worktree."""
    if pid is None:
        return False
    try:
        pid = int(pid)
    except (TypeError, ValueError):
        return False
    if pid <= 0:
        return False

    if os.name == "nt":
        import ctypes

        process_query_limited_information = 0x1000
        handle = ctypes.windll.kernel32.OpenProcess(  # type: ignore[attr-defined]
            process_query_limited_information, False, pid
        )
        if not handle:
            return False
        exit_code = ctypes.c_ulong()
        still_active = 259
        ok = ctypes.windll.kernel32.GetExitCodeProcess(  # type: ignore[attr-defined]
            handle, ctypes.byref(exit_code)
        )
        ctypes.windll.kernel32.CloseHandle(handle)  # type: ignore[attr-defined]
        if not ok:
            return True
        return exit_code.value == still_active

    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    except OSError:
        return False
    return True


def normalize_slug(value: str) -> str:
    """Turn an arbitrary branch/task name into a filesystem-safe slug."""
    text = str(value).strip().strip("/")
    for prefix in ("origin/", "refs/heads/"):
        if text.startswith(prefix):
            text = text[len(prefix) :]
    if text.startswith(BRANCH_PREFIX):
        text = text[len(BRANCH_PREFIX) :]
    text = text.replace("/", "-")
    text = _SLUG_INVALID_RE.sub("-", text).strip("-.")
    if not text:
        raise ValueError(f"Cannot derive a worktree slug from {value!r}.")
    return text


@dataclass
class WorktreeInfo:
    """One worktree handed out to an agent (a row in ``data/worktrees.json``)."""

    slug: str
    branch: str
    path: Path
    created_at: str
    pid: Optional[int] = None
    state: str = STATE_ACTIVE
    base_ref: str = "main"
    variant: Optional[int] = None
    # Filled in by the calling code (queue_manager and friends).
    agent_id: Optional[str] = None
    task_slug: Optional[str] = None
    pr_url: Optional[str] = None
    released_at: Optional[str] = None
    error: Optional[str] = None
    # Registry/reality discrepancies, refreshed by list_all().
    missing_on_disk: bool = False
    not_in_git: bool = False
    not_in_registry: bool = False

    def to_dict(self) -> Dict[str, Any]:
        data = {f.name: getattr(self, f.name) for f in fields(self)}
        data["path"] = str(self.path)
        return data

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "WorktreeInfo":
        known = {f.name for f in fields(cls)}
        kwargs = {k: v for k, v in data.items() if k in known}
        kwargs["path"] = Path(str(kwargs.get("path", "")))
        kwargs.setdefault("created_at", _isoformat(_utc_now()))
        return cls(**kwargs)


@dataclass
class GitWorktree:
    """One entry from ``git worktree list --porcelain``."""

    path: Path
    head: Optional[str] = None
    branch: Optional[str] = None
    detached: bool = False
    bare: bool = False
    locked: bool = False
    prunable: bool = False


class WorktreeOrchestrator:
    """Create, release and reap per-agent git worktrees."""

    def __init__(
        self,
        repo_path: Path,
        worktree_root: Path | None = None,
        *,
        registry_path: Path | None = None,
    ) -> None:
        self.repo_path = Path(repo_path).resolve()
        self.worktree_root = (
            Path(worktree_root).resolve()
            if worktree_root is not None
            else self.repo_path / DEFAULT_WORKTREE_DIRNAME
        )
        self.registry_path = (
            Path(registry_path).resolve()
            if registry_path is not None
            else self.repo_path / DEFAULT_REGISTRY_RELPATH
        )

    # -- git plumbing -----------------------------------------------------

    def _run_git(
        self, args: List[str], *, check: bool = True
    ) -> subprocess.CompletedProcess:
        cmd = ["git", *args]
        LOGGER.debug("running %s (cwd=%s)", cmd, self.repo_path)
        try:
            proc = subprocess.run(
                cmd,
                cwd=str(self.repo_path),
                capture_output=True,
                text=True,
                timeout=GIT_TIMEOUT,
                shell=False,
                check=False,
            )
        except FileNotFoundError as exc:
            raise WorktreeError("git executable not found in PATH.") from exc
        except subprocess.TimeoutExpired as exc:
            raise WorktreeError(
                f"git {' '.join(args)} timed out after {GIT_TIMEOUT}s."
            ) from exc

        if check and proc.returncode != 0:
            raise WorktreeError(
                f"git {' '.join(args)} failed ({proc.returncode}): "
                f"{(proc.stderr or proc.stdout).strip()}"
            )
        return proc

    def git_worktrees(self) -> List[GitWorktree]:
        """Parse ``git worktree list --porcelain``."""
        proc = self._run_git(["worktree", "list", "--porcelain"], check=False)
        if proc.returncode != 0:
            LOGGER.warning(
                "git worktree list failed (%s): %s",
                proc.returncode,
                (proc.stderr or proc.stdout).strip(),
            )
            return []

        entries: List[GitWorktree] = []
        current: Optional[GitWorktree] = None
        for raw_line in (proc.stdout or "").splitlines():
            line = raw_line.rstrip()
            if not line:
                if current is not None:
                    entries.append(current)
                    current = None
                continue
            key, _, value = line.partition(" ")
            if key == "worktree":
                if current is not None:
                    entries.append(current)
                current = GitWorktree(path=Path(value))
            elif current is None:
                continue
            elif key == "HEAD":
                current.head = value
            elif key == "branch":
                prefix = "refs/heads/"
                current.branch = (
                    value[len(prefix) :] if value.startswith(prefix) else value
                )
            elif key == "detached":
                current.detached = True
            elif key == "bare":
                current.bare = True
            elif key == "locked":
                current.locked = True
            elif key == "prunable":
                current.prunable = True
        if current is not None:
            entries.append(current)
        return entries

    def _resolved_git_worktrees(self) -> Dict[Path, GitWorktree]:
        out: Dict[Path, GitWorktree] = {}
        for entry in self.git_worktrees():
            try:
                out[entry.path.resolve()] = entry
            except OSError:
                out[entry.path] = entry
        return out

    def _branch_exists(self, branch: str) -> bool:
        proc = self._run_git(
            ["rev-parse", "--verify", "--quiet", f"refs/heads/{branch}"],
            check=False,
        )
        return proc.returncode == 0 and bool((proc.stdout or "").strip())

    def _worktree_path_for_branch(self, branch: str) -> Optional[Path]:
        for entry in self.git_worktrees():
            if entry.branch == branch:
                return entry.path
        return None

    # -- registry ---------------------------------------------------------

    def _load_registry(self) -> Dict[str, WorktreeInfo]:
        registry: Dict[str, WorktreeInfo] = {}
        try:
            raw = self.registry_path.read_text(encoding="utf-8")
        except FileNotFoundError:
            return registry
        except OSError as exc:
            LOGGER.warning("cannot read registry %s: %s", self.registry_path, exc)
            return registry

        try:
            payload = json.loads(raw) if raw.strip() else {}
        except json.JSONDecodeError as exc:
            LOGGER.warning("registry %s is corrupt (%s); ignoring", self.registry_path, exc)
            return registry

        for item in payload.get("worktrees") or []:
            if not isinstance(item, dict) or not item.get("slug"):
                continue
            info = WorktreeInfo.from_dict(item)
            registry[info.slug] = info
        return registry

    def _save_registry(self, registry: Dict[str, WorktreeInfo]) -> None:
        payload = {
            "version": REGISTRY_VERSION,
            "updated_at": _isoformat(_utc_now()),
            "worktrees": [info.to_dict() for info in registry.values()],
        }
        self.registry_path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.registry_path.with_name(
            f"{self.registry_path.name}.tmp.{os.getpid()}"
        )
        tmp.write_text(
            json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8"
        )
        os.replace(tmp, self.registry_path)

    # -- public API -------------------------------------------------------

    @staticmethod
    def entry_slug(slug: str, variant: int | None = None) -> str:
        """Registry key (and directory name) for a slug/variant pair."""
        base = normalize_slug(slug)
        return base if variant is None else f"{base}-variant-{int(variant)}"

    def branch_for(self, slug: str, variant: int | None = None) -> str:
        return BRANCH_PREFIX + self.entry_slug(slug, variant)

    def path_for(self, slug: str, variant: int | None = None) -> Path:
        """Absolute path that should be handed to the agent."""
        key = self.entry_slug(slug, variant)
        recorded = self._load_registry().get(key)
        if recorded is not None:
            return recorded.path
        return self.worktree_root / key

    def get(self, slug: str, variant: int | None = None) -> Optional[WorktreeInfo]:
        return self._load_registry().get(self.entry_slug(slug, variant))

    def create(
        self,
        slug: str,
        base_ref: str = "main",
        variant: int | None = None,
    ) -> WorktreeInfo:
        """Hand out an isolated worktree on ``cursor/<slug>``.

        Idempotent: calling it twice with the same slug reuses the existing
        branch and worktree instead of failing or creating a second branch.
        """
        key = self.entry_slug(slug, variant)
        branch = BRANCH_PREFIX + key
        path = self.worktree_root / key

        registry = self._load_registry()
        existing = registry.get(key)
        if existing is not None and existing.state in LIVE_STATES:
            path = existing.path

        self.worktree_root.mkdir(parents=True, exist_ok=True)

        registered = path.resolve() in self._resolved_git_worktrees()
        if not registered or not path.exists():
            # Drop stale administrative entries left behind by a crashed runner.
            self._run_git(["worktree", "prune"], check=False)
            if self._branch_exists(branch):
                checked_out = self._worktree_path_for_branch(branch)
                if checked_out is not None:
                    path = checked_out
                else:
                    self._run_git(["worktree", "add", str(path), branch])
            else:
                self._run_git(
                    ["worktree", "add", str(path), "-b", branch, base_ref]
                )

        now = _isoformat(_utc_now())
        if existing is None:
            info = WorktreeInfo(
                slug=key,
                branch=branch,
                path=path,
                created_at=now,
                pid=os.getpid(),
                state=STATE_ACTIVE,
                base_ref=base_ref,
                variant=variant,
            )
        else:
            info = existing
            info.branch = branch
            info.path = path
            info.base_ref = base_ref
            info.variant = variant
            info.pid = os.getpid()
            info.state = STATE_ACTIVE
            info.released_at = None
            info.error = None
        info.missing_on_disk = False
        info.not_in_git = False

        registry[key] = info
        self._save_registry(registry)
        LOGGER.info("worktree ready: %s -> %s (%s)", branch, info.path, info.state)
        return info

    def update(self, slug: str, **changes: Any) -> Optional[WorktreeInfo]:
        """Record what the agent did in this worktree (agent_id, pr_url, …)."""
        registry = self._load_registry()
        info = registry.get(slug) or registry.get(self.entry_slug(slug))
        if info is None:
            LOGGER.warning("update: unknown worktree slug %r", slug)
            return None
        allowed = {f.name for f in fields(WorktreeInfo)} - {"slug", "path"}
        for key, value in changes.items():
            if key in allowed:
                setattr(info, key, value)
            else:
                LOGGER.warning("update: ignoring unsupported field %r", key)
        registry[info.slug] = info
        self._save_registry(registry)
        return info

    def release(
        self,
        slug: str,
        keep: bool = False,
        *,
        delete_branch: bool = False,
        force: bool = False,
    ) -> bool:
        """Take the worktree back. Never raises; returns success.

        ``keep=True`` leaves the directory on disk and marks it ``kept``.
        The branch is only deleted when ``delete_branch`` is explicitly set, so
        uncommitted agent work is never thrown away by accident.
        """
        registry = self._load_registry()
        key = slug if slug in registry else self.entry_slug(slug)
        info = registry.get(key)
        if info is None:
            LOGGER.warning("release: unknown worktree slug %r", slug)
            return False

        if keep:
            info.state = STATE_KEPT
            info.error = None
            info.released_at = _isoformat(_utc_now())
            registry[info.slug] = info
            self._save_registry(registry)
            LOGGER.info("worktree kept on disk: %s (%s)", info.slug, info.path)
            return True

        args = ["worktree", "remove"]
        if force:
            args.append("--force")
        args.append(str(info.path))

        failure: Optional[str] = None
        try:
            proc = self._run_git(args, check=False)
        except WorktreeError as exc:
            failure = str(exc)
        else:
            if proc.returncode != 0:
                failure = (proc.stderr or proc.stdout).strip() or f"exit {proc.returncode}"

        if failure is not None and info.path.exists():
            # Locked, dirty or busy worktree: keep the directory, flag the entry.
            LOGGER.error("release failed for %s: %s", info.slug, failure)
            info.state = STATE_ERROR
            info.error = failure
            registry[info.slug] = info
            self._save_registry(registry)
            return False

        if failure is not None:
            LOGGER.warning(
                "worktree %s already gone from disk (%s); pruning", info.slug, failure
            )

        self._run_git(["worktree", "prune"], check=False)

        if delete_branch:
            branch_proc = self._run_git(["branch", "-D", info.branch], check=False)
            if branch_proc.returncode != 0:
                LOGGER.warning(
                    "could not delete branch %s: %s",
                    info.branch,
                    (branch_proc.stderr or branch_proc.stdout).strip(),
                )

        info.state = STATE_RELEASED
        info.error = None
        info.released_at = _isoformat(_utc_now())
        info.missing_on_disk = False
        info.not_in_git = False
        registry[info.slug] = info
        self._save_registry(registry)
        LOGGER.info("worktree released: %s", info.slug)
        return True

    def reap(self, older_than_hours: int = 24) -> List[WorktreeInfo]:
        """Release worktrees whose owner died or that outlived the threshold."""
        cutoff = _utc_now() - timedelta(hours=float(older_than_hours))
        reaped: List[WorktreeInfo] = []

        for info in list(self._load_registry().values()):
            if info.state in (STATE_KEPT, STATE_RELEASED):
                continue
            created = _parse_iso(info.created_at)
            owner_dead = not _pid_alive(info.pid)
            too_old = created is not None and created < cutoff
            if not (owner_dead or too_old):
                continue
            reason = "dead owner" if owner_dead else "older than threshold"
            LOGGER.info("reaping %s (%s)", info.slug, reason)
            if self.release(info.slug):
                refreshed = self._load_registry().get(info.slug)
                reaped.append(refreshed or info)
        return reaped

    def list_all(self) -> List[WorktreeInfo]:
        """Registry entries reconciled against ``git worktree list``."""
        registry = self._load_registry()
        git_entries = self._resolved_git_worktrees()
        main_path = self.repo_path
        known_paths = set()
        changed = False

        for info in registry.values():
            try:
                resolved = info.path.resolve()
            except OSError:
                resolved = info.path
            known_paths.add(resolved)

            if info.state in LIVE_STATES:
                missing_on_disk = not info.path.exists()
                not_in_git = resolved not in git_entries
            else:
                missing_on_disk = False
                not_in_git = False

            if (info.missing_on_disk, info.not_in_git) != (missing_on_disk, not_in_git):
                info.missing_on_disk = missing_on_disk
                info.not_in_git = not_in_git
                changed = True

        if changed:
            self._save_registry(registry)

        result = list(registry.values())
        for resolved, entry in git_entries.items():
            if resolved == main_path or resolved in known_paths:
                continue
            if entry.bare:
                continue
            result.append(
                WorktreeInfo(
                    slug=entry.path.name,
                    branch=entry.branch or "(detached)",
                    path=entry.path,
                    created_at="",
                    pid=None,
                    state=STATE_UNREGISTERED,
                    not_in_registry=True,
                )
            )
        return result

    # -- doctor -----------------------------------------------------------

    def doctor(self) -> Dict[str, Any]:
        """Cross-check the registry against git and the host's Scheduled Tasks."""
        entries = self.list_all()
        scheduled, scheduled_note = scheduled_task_names()
        issues: List[Dict[str, str]] = []

        for info in entries:
            if info.not_in_registry:
                issues.append(
                    {
                        "slug": info.slug,
                        "kind": "not_in_registry",
                        "detail": f"git tracks {info.path} but the registry does not",
                    }
                )
                continue
            if info.missing_on_disk:
                issues.append(
                    {
                        "slug": info.slug,
                        "kind": "missing_on_disk",
                        "detail": f"{info.path} is gone but state is '{info.state}'",
                    }
                )
            if info.not_in_git:
                issues.append(
                    {
                        "slug": info.slug,
                        "kind": "not_in_git",
                        "detail": f"{info.path} is not a registered git worktree",
                    }
                )
            if info.state == STATE_ERROR:
                issues.append(
                    {
                        "slug": info.slug,
                        "kind": "release_error",
                        "detail": info.error or "previous release failed",
                    }
                )
            if info.state == STATE_ACTIVE and not _pid_alive(info.pid):
                issues.append(
                    {
                        "slug": info.slug,
                        "kind": "dead_owner",
                        "detail": f"owner pid {info.pid} is not running; reap candidate",
                    }
                )
            if scheduled is not None and info.state == STATE_ACTIVE:
                if not any(info.slug in name for name in scheduled):
                    issues.append(
                        {
                            "slug": info.slug,
                            "kind": "no_scheduled_task",
                            "detail": "no Scheduled Task references this worktree",
                        }
                    )

        return {
            "repo_path": str(self.repo_path),
            "worktree_root": str(self.worktree_root),
            "registry_path": str(self.registry_path),
            "entries": entries,
            "issues": issues,
            "scheduled_tasks": scheduled,
            "scheduled_tasks_note": scheduled_note,
        }


def scheduled_task_names() -> Tuple[Optional[List[str]], Optional[str]]:
    """Windows Scheduled Task names, or ``(None, reason)`` when unavailable."""
    if os.name != "nt":
        return None, "Scheduled Tasks check skipped (not a Windows host)"
    try:
        proc = subprocess.run(
            ["schtasks", "/query", "/fo", "csv", "/nh"],
            capture_output=True,
            timeout=GIT_TIMEOUT,
            shell=False,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        return None, f"Scheduled Tasks check failed: {exc}"
    stdout = decode_console_output(proc.stdout)
    stderr = decode_console_output(proc.stderr)
    if proc.returncode != 0:
        return None, (
            "Scheduled Tasks check failed: "
            f"{(stderr or stdout).strip() or proc.returncode}"
        )

    names: List[str] = []
    for row in csv.reader(stdout.splitlines()):
        if row and row[0].strip():
            names.append(row[0].strip().lstrip("\\"))
    return names, None


# -- CLI -----------------------------------------------------------------


def _format_table(headers: List[str], rows: List[List[str]]) -> str:
    widths = [len(h) for h in headers]
    for row in rows:
        for i, cell in enumerate(row):
            widths[i] = max(widths[i], len(cell))
    lines = ["  ".join(h.ljust(widths[i]) for i, h in enumerate(headers)).rstrip()]
    lines.append("  ".join("-" * widths[i] for i in range(len(headers))))
    for row in rows:
        lines.append("  ".join(cell.ljust(widths[i]) for i, cell in enumerate(row)).rstrip())
    return "\n".join(lines)


def _flags(info: WorktreeInfo) -> str:
    marks = []
    if info.missing_on_disk:
        marks.append("missing_on_disk")
    if info.not_in_git:
        marks.append("not_in_git")
    if info.not_in_registry:
        marks.append("not_in_registry")
    return ",".join(marks) or "-"


def _print_entries(entries: Iterable[WorktreeInfo]) -> None:
    rows = [
        [
            info.slug,
            info.state,
            info.branch,
            str(info.path),
            str(info.pid or "-"),
            info.agent_id or "-",
            info.pr_url or "-",
            _flags(info),
        ]
        for info in entries
    ]
    if not rows:
        print("No worktrees registered.")
        return
    print(
        _format_table(
            ["SLUG", "STATE", "BRANCH", "PATH", "PID", "AGENT", "PR", "FLAGS"], rows
        )
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m scripts.worktree_orchestrator",
        description="Manage isolated git worktrees for parallel Cursor agents.",
    )
    parser.add_argument(
        "--repo",
        type=Path,
        default=Path(__file__).resolve().parent.parent,
        help="Repository root (default: this checkout).",
    )
    parser.add_argument(
        "--worktree-root",
        type=Path,
        default=None,
        help="Where worktrees live (default: <repo>/.worktrees).",
    )
    parser.add_argument(
        "--registry",
        type=Path,
        default=None,
        help="Registry path (default: <repo>/data/worktrees.json).",
    )
    parser.add_argument("--verbose", action="store_true", help="Enable debug logging.")

    sub = parser.add_subparsers(dest="command", required=True)

    create_p = sub.add_parser("create", help="Create or reuse a worktree.")
    create_p.add_argument("--slug", required=True)
    create_p.add_argument("--base-ref", default="main")
    create_p.add_argument("--variant", type=int, default=None)

    release_p = sub.add_parser("release", help="Release a worktree.")
    release_p.add_argument("--slug", required=True)
    release_p.add_argument(
        "--keep", action="store_true", help="Leave the directory on disk (state=kept)."
    )
    release_p.add_argument(
        "--delete-branch",
        action="store_true",
        help="Also delete cursor/<slug> (off by default: it may hold unpushed work).",
    )
    release_p.add_argument(
        "--force", action="store_true", help="Pass --force to git worktree remove."
    )

    reap_p = sub.add_parser("reap", help="Release orphaned worktrees.")
    reap_p.add_argument("--older-than-hours", type=int, default=24)

    sub.add_parser("list", help="List registered worktrees.")
    sub.add_parser("doctor", help="Report registry / git / Scheduled Task drift.")

    return parser


def main(argv: List[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(levelname)s %(name)s: %(message)s",
    )

    orchestrator = WorktreeOrchestrator(
        args.repo, args.worktree_root, registry_path=args.registry
    )

    if args.command == "create":
        try:
            info = orchestrator.create(args.slug, args.base_ref, args.variant)
        except (WorktreeError, ValueError) as exc:
            print(f"create failed: {exc}", file=sys.stderr)
            return 1
        print(f"slug:   {info.slug}")
        print(f"branch: {info.branch}")
        print(f"path:   {info.path}")
        print(f"state:  {info.state}")
        return 0

    if args.command == "release":
        ok = orchestrator.release(
            args.slug,
            keep=args.keep,
            delete_branch=args.delete_branch,
            force=args.force,
        )
        info = orchestrator.get(args.slug)
        state = info.state if info else "unknown"
        print(f"release {args.slug}: {'ok' if ok else 'failed'} (state={state})")
        return 0 if ok else 1

    if args.command == "reap":
        reaped = orchestrator.reap(args.older_than_hours)
        if not reaped:
            print("Nothing to reap.")
            return 0
        _print_entries(reaped)
        print(f"Reaped {len(reaped)} worktree(s).")
        return 0

    if args.command == "list":
        _print_entries(orchestrator.list_all())
        return 0

    if args.command == "doctor":
        report = orchestrator.doctor()
        print(f"repo:     {report['repo_path']}")
        print(f"root:     {report['worktree_root']}")
        print(f"registry: {report['registry_path']}")
        if report["scheduled_tasks_note"]:
            print(f"note:     {report['scheduled_tasks_note']}")
        print()
        _print_entries(report["entries"])
        print()
        issues = report["issues"]
        if not issues:
            print("No discrepancies found / расхождений нет")
            return 0
        print(
            _format_table(
                ["SLUG", "ISSUE", "DETAIL"],
                [[i["slug"], i["kind"], i["detail"]] for i in issues],
            )
        )
        print(f"{len(issues)} discrepancy(ies) found.")
        return 1

    return 2


if __name__ == "__main__":
    raise SystemExit(main())
