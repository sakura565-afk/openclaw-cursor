#!/usr/bin/env python3
"""Inspect and clean up merged GitHub pull requests and remote branches."""

from __future__ import annotations

import argparse
from collections.abc import Mapping, Sequence
from dataclasses import asdict, dataclass, replace
import json
import os
from pathlib import Path
import re
import subprocess
import sys
from typing import Any, Literal
import urllib.error
import urllib.request
from urllib.parse import quote


DEFAULT_REPO = "sakura565-afk/openclaw-cursor"
DEFAULT_MAIN_REF = "main"
STALE_BEHIND_THRESHOLD = 50


def default_token_file() -> Path | None:
    """Token file from ``GITHUB_TOKEN_FILE``; no machine-specific path is baked in.

    A hardcoded absolute path would break the repository's own
    ``tests/test_no_absolute_paths.py`` guard and CI on any other host.
    """
    raw = os.environ.get("GITHUB_TOKEN_FILE", "").strip()
    return Path(raw) if raw else None


Action = Literal["close", "delete", "keep"]


@dataclass(frozen=True)
class PullRequest:
    """The pull-request fields needed to make a cleanup decision."""

    number: int
    title: str
    head_branch: str
    head_repo: str
    draft: bool = False
    mergeable: str | None = None
    merge_state_status: str | None = None


@dataclass(frozen=True)
class BranchStatus:
    """A remote branch and its commit count relative to main."""

    name: str
    ahead_by: int
    behind_by: int = 0


@dataclass(frozen=True)
class BranchIntegration:
    """Local git signals beyond GitHub ``ahead_by`` (cherry-pick / squash / gaps)."""

    behind_by: int = 0
    cherry_equivalent: int | None = None
    cherry_unique: int | None = None
    paths_missing_on_main: tuple[str, ...] = ()
    main_integration_commit: str = ""


@dataclass(frozen=True)
class PullRequestDecision:
    """The planned action for an open pull request."""

    number: int
    title: str
    head_branch: str
    action: Action
    reason: str
    comment_body: str = ""


@dataclass(frozen=True)
class BranchDecision:
    """The planned action for a remote branch."""

    name: str
    ahead_by: int
    merged: bool
    open_prs: list[int]
    action: Action
    reason: str


@dataclass(frozen=True)
class HygienePlan:
    """A complete, side-effect-free repository cleanup plan."""

    repo: str
    dry_run: bool
    pull_requests: list[PullRequestDecision]
    branches: list[BranchDecision]


class GitHubClient:
    """Minimal GitHub REST client using only the Python standard library."""

    api_base = "https://api.github.com"

    def __init__(self, *, repo: str, token: str, timeout: float = 30.0) -> None:
        self.repo = repo
        self.token = token
        self.timeout = timeout

    def _request(
        self,
        method: str,
        path: str,
        payload: dict[str, object] | None = None,
    ) -> object:
        """Send one API request; credentials are kept in headers only."""
        body = None if payload is None else json.dumps(payload).encode("utf-8")
        request = urllib.request.Request(
            f"{self.api_base}{path}",
            data=body,
            method=method,
            headers={
                "Accept": "application/vnd.github+json",
                "Authorization": f"Bearer {self.token}",
                "Content-Type": "application/json",
                "User-Agent": "openclaw-repo-hygiene",
                "X-GitHub-Api-Version": "2022-11-28",
            },
        )
        with urllib.request.urlopen(request, timeout=self.timeout) as response:
            response_body = response.read()
        if not response_body:
            return None
        return json.loads(response_body.decode("utf-8"))

    def _list_paginated(self, path: str) -> list[object]:
        """Fetch every page of a GitHub list endpoint."""
        items: list[object] = []
        page = 1
        while True:
            separator = "&" if "?" in path else "?"
            payload = self._request(
                "GET",
                f"{path}{separator}per_page=100&page={page}",
            )
            if not isinstance(payload, list):
                raise ValueError("GitHub list endpoint returned a non-list response")
            items.extend(payload)
            if len(payload) < 100:
                return items
            page += 1

    def list_open_pull_requests(self) -> list[PullRequest]:
        """Return all open pull requests with their head repository and branch."""
        payload = self._list_paginated(f"/repos/{self.repo}/pulls?state=open")
        pull_requests: list[PullRequest] = []
        for item in payload:
            if not isinstance(item, Mapping):
                raise ValueError("GitHub pulls response contains an invalid entry")
            head = item.get("head")
            if not isinstance(head, Mapping):
                raise ValueError("GitHub pull request has no valid head")
            head_repo = head.get("repo")
            repo_name = ""
            if isinstance(head_repo, Mapping):
                repo_name = str(head_repo.get("full_name") or "")
            number = item.get("number")
            title = item.get("title")
            branch = head.get("ref")
            if type(number) is not int or not isinstance(title, str) or not isinstance(branch, str):
                raise ValueError("GitHub pull request is missing required fields")
            draft = item.get("draft") is True
            mergeable = item.get("mergeable")
            merge_state = item.get("merge_state_status")
            pull_requests.append(
                PullRequest(
                    number=number,
                    title=title,
                    head_branch=branch,
                    head_repo=repo_name,
                    draft=draft,
                    mergeable=str(mergeable) if mergeable is not None else None,
                    merge_state_status=str(merge_state) if merge_state is not None else None,
                )
            )
        return pull_requests

    def get_pull_request(self, number: int) -> PullRequest:
        """Fetch one pull request including mergeability fields."""
        payload = self._request("GET", f"/repos/{self.repo}/pulls/{number}")
        if not isinstance(payload, Mapping):
            raise ValueError("GitHub pull response is not an object")
        head = payload.get("head")
        if not isinstance(head, Mapping):
            raise ValueError("GitHub pull request has no valid head")
        head_repo = head.get("repo")
        repo_name = ""
        if isinstance(head_repo, Mapping):
            repo_name = str(head_repo.get("full_name") or "")
        branch = head.get("ref")
        title = payload.get("title")
        number_value = payload.get("number")
        if (
            type(number_value) is not int
            or not isinstance(title, str)
            or not isinstance(branch, str)
        ):
            raise ValueError("GitHub pull request is missing required fields")
        mergeable = payload.get("mergeable")
        merge_state = payload.get("merge_state_status")
        return PullRequest(
            number=number_value,
            title=title,
            head_branch=branch,
            head_repo=repo_name,
            draft=payload.get("draft") is True,
            mergeable=str(mergeable) if mergeable is not None else None,
            merge_state_status=str(merge_state) if merge_state is not None else None,
        )

    def list_remote_branches(self) -> list[str]:
        """Return all remote branch names."""
        payload = self._list_paginated(f"/repos/{self.repo}/branches")
        branches: list[str] = []
        for item in payload:
            if not isinstance(item, Mapping) or not isinstance(item.get("name"), str):
                raise ValueError("GitHub branches response contains an invalid entry")
            branches.append(str(item["name"]))
        return branches

    def compare_with_main(self, branch: str) -> int:
        """Return how many commits a branch has that are absent from main."""
        return self.compare_with_main_details(branch).ahead_by

    def compare_with_main_details(
        self,
        branch: str,
        *,
        main_ref: str = DEFAULT_MAIN_REF,
    ) -> BranchStatus:
        """Return ahead/behind counts from the GitHub three-dot compare."""
        encoded_branch = quote(branch, safe="/")
        payload = self._request(
            "GET",
            f"/repos/{self.repo}/compare/{main_ref}...{encoded_branch}",
        )
        if not isinstance(payload, Mapping):
            raise ValueError("GitHub compare endpoint returned a non-object response")
        ahead_by = compare_ahead_by(payload)
        behind_by = compare_behind_by(payload)
        return BranchStatus(name=branch, ahead_by=ahead_by, behind_by=behind_by)

    def file_exists_on_main(self, path: str, *, main_ref: str = DEFAULT_MAIN_REF) -> bool:
        """Return whether ``path`` exists at ``main_ref``."""
        encoded = quote(path, safe="/")
        request = urllib.request.Request(
            f"{self.api_base}/repos/{self.repo}/contents/{encoded}?ref={quote(main_ref)}",
            method="GET",
            headers={
                "Accept": "application/vnd.github+json",
                "Authorization": f"Bearer {self.token}",
                "User-Agent": "openclaw-repo-hygiene",
                "X-GitHub-Api-Version": "2022-11-28",
            },
        )
        try:
            with urllib.request.urlopen(request, timeout=self.timeout) as response:
                return 200 <= response.status < 300
        except urllib.error.HTTPError as exc:
            if exc.code == 404:
                return False
            raise

    def paths_added_by_branch_missing_on_main(
        self,
        branch: str,
        *,
        main_ref: str = DEFAULT_MAIN_REF,
    ) -> list[str]:
        """Paths newly introduced on the branch that are still absent from main."""
        encoded_branch = quote(branch, safe="/")
        payload = self._request(
            "GET",
            f"/repos/{self.repo}/compare/{main_ref}...{encoded_branch}",
        )
        if not isinstance(payload, Mapping):
            raise ValueError("GitHub compare endpoint returned a non-object response")
        files = payload.get("files")
        if not isinstance(files, list):
            return []
        missing: list[str] = []
        for item in files:
            if not isinstance(item, Mapping):
                continue
            status = str(item.get("status") or "")
            filename = item.get("filename")
            if status not in {"added", "renamed"} or not isinstance(filename, str):
                continue
            if not self.file_exists_on_main(filename, main_ref=main_ref):
                missing.append(filename)
        return missing

    def close_pull_request(self, number: int) -> None:
        """Close an open pull request."""
        self._request(
            "PATCH",
            f"/repos/{self.repo}/pulls/{number}",
            {"state": "closed"},
        )

    def comment_on_pull_request(self, number: int, body: str) -> None:
        """Post an issue comment on a pull request."""
        self._request(
            "POST",
            f"/repos/{self.repo}/issues/{number}/comments",
            {"body": body},
        )

    def delete_branch(self, branch: str) -> None:
        """Delete a remote branch, preserving slashes in its URL path."""
        encoded_branch = quote(branch, safe="/")
        self._request(
            "DELETE",
            f"/repos/{self.repo}/git/refs/heads/{encoded_branch}",
        )


def compare_ahead_by(payload: Mapping[str, Any]) -> int:
    """Return the number of commits a branch has that are absent from main."""
    value = payload.get("ahead_by")
    if type(value) is not int or value < 0:
        raise ValueError("GitHub compare response has an invalid ahead_by value")
    return value


def compare_behind_by(payload: Mapping[str, Any]) -> int:
    """Return how many commits on main are absent from the branch."""
    value = payload.get("behind_by")
    if type(value) is not int or value < 0:
        raise ValueError("GitHub compare response has an invalid behind_by value")
    return value


def repository_root() -> Path | None:
    """Return the checkout root when this script runs inside a git clone."""
    start = Path(__file__).resolve().parent.parent
    candidate = start
    for _ in range(4):
        if (candidate / ".git").exists():
            return candidate
        if candidate.parent == candidate:
            break
        candidate = candidate.parent
    return None


def _run_git(args: Sequence[str], *, cwd: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", *args],
        cwd=cwd,
        capture_output=True,
        text=True,
        check=False,
    )


def git_cherry_counts(
    branch: str,
    *,
    cwd: Path,
    main_ref: str = f"origin/{DEFAULT_MAIN_REF}",
) -> tuple[int, int] | None:
    """Count patch-equivalent (``-``) and unique (``+``) commits like ``git cherry``."""
    head_ref = f"origin/{branch}"
    if _run_git(["rev-parse", "--verify", head_ref], cwd=cwd).returncode != 0:
        return None
    if _run_git(["rev-parse", "--verify", main_ref], cwd=cwd).returncode != 0:
        return None
    proc = _run_git(["cherry", main_ref, head_ref], cwd=cwd)
    if proc.returncode != 0:
        return None
    equivalent = 0
    unique = 0
    for line in proc.stdout.splitlines():
        if line.startswith("-"):
            equivalent += 1
        elif line.startswith("+"):
            unique += 1
    return equivalent, unique


def git_paths_missing_on_main(
    branch: str,
    *,
    cwd: Path,
    main_ref: str = f"origin/{DEFAULT_MAIN_REF}",
) -> list[str]:
    """Return paths added on the branch relative to main that main still lacks."""
    head_ref = f"origin/{branch}"
    proc = _run_git(
        [
            "diff",
            "--name-only",
            "--diff-filter=A",
            f"{main_ref}...{head_ref}",
        ],
        cwd=cwd,
    )
    if proc.returncode != 0:
        return []
    missing: list[str] = []
    for path in proc.stdout.splitlines():
        path = path.strip()
        if not path:
            continue
        exists = _run_git(["cat-file", "-e", f"{main_ref}:{path}"], cwd=cwd)
        if exists.returncode != 0:
            missing.append(path)
    return missing


def git_main_integration_commit(
    pr_number: int,
    *,
    cwd: Path,
    main_ref: str = f"origin/{DEFAULT_MAIN_REF}",
) -> str:
    """Find a short main commit message that references the pull request."""
    proc = _run_git(
        [
            "log",
            main_ref,
            "--format=%h %s",
            f"--grep=#{pr_number}",
            "-i",
            "-n",
            "20",
        ],
        cwd=cwd,
    )
    if proc.returncode != 0 or not proc.stdout.strip():
        proc = _run_git(
            [
                "log",
                main_ref,
                "--format=%h %s",
                f"--grep=PR #{pr_number}",
                "-i",
                "-n",
                "20",
            ],
            cwd=cwd,
        )
    if proc.returncode != 0:
        return ""
    for line in proc.stdout.splitlines():
        if re.search(rf"#\s*{pr_number}\b", line, flags=re.IGNORECASE):
            return line.strip()
    return proc.stdout.splitlines()[0].strip() if proc.stdout.strip() else ""


def gather_branch_integration(
    client: GitHubClient,
    branch: str,
    *,
    pr_number: int | None = None,
    main_ref: str = DEFAULT_MAIN_REF,
) -> BranchIntegration:
    """Merge GitHub compare data with optional local git cherry signals."""
    details = client.compare_with_main_details(branch, main_ref=main_ref)
    missing_api = tuple(client.paths_added_by_branch_missing_on_main(branch, main_ref=main_ref))
    root = repository_root()
    cherry_equivalent: int | None = None
    cherry_unique: int | None = None
    missing_git: list[str] = []
    landmark = ""
    if root is not None:
        cherry = git_cherry_counts(branch, cwd=root)
        if cherry is not None:
            cherry_equivalent, cherry_unique = cherry
        missing_git = git_paths_missing_on_main(branch, cwd=root)
        if pr_number is not None:
            landmark = git_main_integration_commit(pr_number, cwd=root)
    missing = tuple(dict.fromkeys([*missing_git, *missing_api]))
    return BranchIntegration(
        behind_by=details.behind_by,
        cherry_equivalent=cherry_equivalent,
        cherry_unique=cherry_unique,
        paths_missing_on_main=missing,
        main_integration_commit=landmark,
    )


def _classify_branch(
    branch: BranchStatus,
    open_prs: list[int],
    include_merged: bool,
) -> BranchDecision:
    """Classify one branch without performing any side effects."""
    merged = branch.ahead_by == 0
    if branch.name == "main":
        action: Action = "keep"
        reason = "protected default branch"
    elif open_prs:
        action = "keep"
        label = "open PR" if len(open_prs) == 1 else "open PRs"
        numbers = ", ".join(f"#{number}" for number in open_prs)
        reason = f"{label} {numbers}"
    elif not merged:
        action = "keep"
        reason = (
            f"{branch.ahead_by} commit(s) not in main; "
            "merge them first or discard them explicitly"
        )
    elif not include_merged:
        action = "keep"
        reason = "fully merged with main; use --include-merged to enable deletion"
    else:
        action = "delete"
        reason = "fully merged with main (ahead_by=0)"
    return BranchDecision(
        name=branch.name,
        ahead_by=branch.ahead_by,
        merged=merged,
        open_prs=open_prs,
        action=action,
        reason=reason,
    )


def _format_missing_paths(paths: Sequence[str], *, limit: int = 6) -> str:
    if not paths:
        return ""
    shown = ", ".join(paths[:limit])
    if len(paths) > limit:
        shown += f" (+{len(paths) - limit} ещё)"
    return shown


def _classify_pull_request(
    pull_request: PullRequest,
    branches: Mapping[str, BranchStatus],
    repo: str,
    integration: BranchIntegration | None = None,
) -> PullRequestDecision:
    """Classify a pull request using merge refs, patch equivalence, and gaps on main."""
    branch = branches.get(pull_request.head_branch)
    integration = integration or BranchIntegration(
        behind_by=branch.behind_by if branch is not None else 0,
    )
    number = pull_request.number
    landmark = integration.main_integration_commit
    landmark_note = f" (main: {landmark})" if landmark else ""

    def decision(action: Action, reason: str, comment_body: str = "") -> PullRequestDecision:
        return PullRequestDecision(
            number=number,
            title=pull_request.title,
            head_branch=pull_request.head_branch,
            action=action,
            reason=reason,
            comment_body=comment_body,
        )

    if pull_request.head_repo != repo:
        return decision("keep", "head branch belongs to a fork")
    if pull_request.head_branch == DEFAULT_MAIN_REF:
        return decision("keep", "head branch is the protected default branch")
    if branch is None:
        return decision("keep", "head branch was not found among remote branches")
    if branch.ahead_by == 0:
        comment = (
            f"Закрываю PR #{number}: head-ветка полностью достижима из "
            f"`{DEFAULT_MAIN_REF}` (ahead_by=0).{landmark_note}"
        )
        return decision(
            "close",
            f"head branch is fully merged with main (ahead_by=0){landmark_note}",
            comment,
        )

    cherry_eq = integration.cherry_equivalent
    cherry_uniq = integration.cherry_unique
    if (
        cherry_eq is not None
        and cherry_uniq is not None
        and cherry_eq > 0
        and cherry_uniq == 0
    ):
        comment = (
            f"Закрываю PR #{number}: патч ветки уже присутствует в `{DEFAULT_MAIN_REF}` "
            f"(squash/cherry-pick; все {cherry_eq} commit(ов) эквивалентны через `git cherry`)."
            f"{landmark_note}"
        )
        return decision(
            "close",
            (
                "patch equivalent to main via squash/cherry-pick "
                f"({cherry_eq} commit(s); git cherry -)"
            )
            + landmark_note,
            comment,
        )

    missing = list(integration.paths_missing_on_main)
    if missing:
        paths = _format_missing_paths(missing)
        conflict = ""
        if pull_request.mergeable == "CONFLICTING":
            conflict = f" GitHub mergeable=CONFLICTING ({pull_request.merge_state_status or 'n/a'})."
        comment = (
            f"PR #{number} оставляю открытым: в `{DEFAULT_MAIN_REF}` до сих пор нет "
            f"файлов/путей из этой ветки: {paths}."
            f"{conflict} "
            f"Нужно либо влить оставшееся в main, либо осознанно отказаться от этих путей."
        )
        return decision(
            "keep",
            f"paths still absent on main: {paths}{conflict}",
            comment,
        )

    behind = max(integration.behind_by, branch.behind_by)
    stale = behind >= STALE_BEHIND_THRESHOLD and branch.ahead_by > 0
    if stale:
        comment = (
            f"Закрываю PR #{number}: черновик устарел (behind `{DEFAULT_MAIN_REF}` "
            f"на {behind} commit(ов), ahead {branch.ahead_by}). "
            f"Содержимое по тем же путям уже эволюционировало в main; "
            f"уникальных отсутствующих путей не осталось."
            f"{landmark_note}"
        )
        return decision(
            "close",
            f"stale branch superseded by main (behind_by={behind}, ahead_by={branch.ahead_by})"
            + landmark_note,
            comment,
        )

    return decision(
        "keep",
        f"head branch has {branch.ahead_by} commit(s) not in main; no auto-close rule matched",
        (
            f"PR #{number} оставляю открытым: в ветке {branch.ahead_by} commit(ов) "
            f"вне `{DEFAULT_MAIN_REF}`; behind={behind}. Требуется ручной merge/rebase."
        ),
    )


def build_plan(
    *,
    repo: str,
    pull_requests: Sequence[PullRequest],
    branches: Sequence[BranchStatus],
    include_merged: bool = False,
    pr_numbers: set[int] | None = None,
    integration_by_pr: Mapping[int, BranchIntegration] | None = None,
) -> HygienePlan:
    """Build a dry-run plan from open pull requests and compared branches."""
    branch_by_name = {branch.name: branch for branch in branches}
    integrations = integration_by_pr or {}
    pr_decisions = [
        _classify_pull_request(
            pr,
            branch_by_name,
            repo,
            integrations.get(pr.number),
        )
        for pr in sorted(pull_requests, key=lambda item: item.number)
        if pr_numbers is None or pr.number in pr_numbers
    ]
    branch_decisions = []
    for branch in sorted(branches, key=lambda item: item.name):
        open_prs = sorted(
            pr.number
            for pr in pull_requests
            if pr.head_repo == repo and pr.head_branch == branch.name
        )
        branch_decisions.append(_classify_branch(branch, open_prs, include_merged))
    return HygienePlan(
        repo=repo,
        dry_run=True,
        pull_requests=pr_decisions,
        branches=branch_decisions,
    )


def execute_plan(
    client: GitHubClient,
    plan: HygienePlan,
    *,
    max_operations: int | None = None,
    annotate_remaining: bool = False,
) -> list[str]:
    """Apply the mutating actions in a plan and return a concise audit list."""
    if max_operations is not None and max_operations < 0:
        raise ValueError("max_operations must be non-negative")
    operations: list[str] = []
    for pr in plan.pull_requests:
        wants_comment = pr.comment_body.strip()
        if wants_comment and (pr.action == "close" or annotate_remaining):
            if max_operations is not None and len(operations) >= max_operations:
                return operations
            client.comment_on_pull_request(pr.number, pr.comment_body.strip())
            operations.append(f"commented on PR #{pr.number}")
        if pr.action != "close":
            continue
        if max_operations is not None and len(operations) >= max_operations:
            return operations
        client.close_pull_request(pr.number)
        operations.append(f"closed PR #{pr.number}")
    for branch in plan.branches:
        if branch.action != "delete" or branch.name == "main":
            continue
        if max_operations is not None and len(operations) >= max_operations:
            return operations
        client.delete_branch(branch.name)
        operations.append(f"deleted branch {branch.name}")
    return operations


def planned_operations(
    plan: HygienePlan,
    max_operations: int | None = None,
) -> list[str]:
    """Describe the operations a dry run would perform, respecting the cap."""
    operations: list[str] = []
    for pr in plan.pull_requests:
        if pr.action != "close":
            continue
        if max_operations is not None and len(operations) >= max_operations:
            return operations
        operations.append(f"close PR #{pr.number}")
    for branch in plan.branches:
        if branch.action != "delete" or branch.name == "main":
            continue
        if max_operations is not None and len(operations) >= max_operations:
            return operations
        operations.append(f"delete branch {branch.name}")
    return operations


def render_text(plan: HygienePlan, operations: Sequence[str]) -> str:
    """Render a human-readable plan or apply report."""
    mode = "DRY RUN" if plan.dry_run else "APPLY"
    lines = [f"Repository: {plan.repo}", f"Mode: {mode}", "", "Pull requests:"]
    if plan.pull_requests:
        for pr in plan.pull_requests:
            lines.append(
                f"  {pr.action.upper()} #{pr.number} {pr.title} "
                f"[{pr.head_branch}] — {pr.reason}"
            )
            if pr.comment_body.strip():
                lines.append(f"      comment: {pr.comment_body.strip()}")
    else:
        lines.append("  (none selected)")
    lines.extend(["", "Branches:"])
    if plan.branches:
        for branch in plan.branches:
            lines.append(
                f"  {branch.action.upper()} {branch.name} — {branch.reason}"
            )
    else:
        lines.append("  (none)")
    label = "Planned operations" if plan.dry_run else "Applied operations"
    lines.extend(["", f"{label} ({len(operations)}):"])
    lines.extend(f"  - {operation}" for operation in operations)
    if not operations:
        lines.append("  (none)")
    if plan.dry_run:
        lines.extend(["", "Dry run only; no changes were made. Use --apply to execute."])
    return "\n".join(lines)


def _non_negative_int(value: str) -> int:
    """Parse a non-negative integer for argparse."""
    number = int(value)
    if number < 0:
        raise argparse.ArgumentTypeError("must be non-negative")
    return number


def build_parser() -> argparse.ArgumentParser:
    """Create the repository hygiene command-line parser."""
    parser = argparse.ArgumentParser(
        description=(
            "Close PRs whose head branch is merged into main and optionally "
            "delete merged remote branches. Dry run by default."
        )
    )
    parser.add_argument("--repo", default=DEFAULT_REPO, help="repository as owner/name")
    parser.add_argument(
        "--token-file",
        type=Path,
        default=None,
        help="file containing a GitHub token (default: $GITHUB_TOKEN_FILE)",
    )
    parser.add_argument(
        "--apply",
        action="store_true",
        help="perform PATCH/DELETE operations (default: dry run only)",
    )
    parser.add_argument(
        "--pr",
        type=int,
        nargs="+",
        help="limit pull-request actions to these PR numbers",
    )
    parser.add_argument(
        "--max",
        type=_non_negative_int,
        help="maximum combined number of PR and branch operations",
    )
    parser.add_argument(
        "--include-merged",
        action="store_true",
        help="include fully merged branches as deletion candidates",
    )
    parser.add_argument("--json", action="store_true", help="emit machine-readable JSON")
    parser.add_argument(
        "--annotate-remaining",
        action="store_true",
        help="when applying, also comment on PRs left open (default with --apply)",
    )
    parser.add_argument(
        "--no-annotate-remaining",
        action="store_true",
        help="with --apply, do not comment on PRs that stay open",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    """Run the repository hygiene CLI."""
    args = build_parser().parse_args(argv)
    try:
        token = ""
        token_file = args.token_file or default_token_file()
        if token_file is not None:
            token = token_file.read_text(encoding="utf-8-sig").strip()
            if not token:
                raise ValueError(f"token file is empty: {token_file}")
        if not token:
            token = os.environ.get("GITHUB_TOKEN", "").strip()
        if not token:
            raise ValueError(
                "no GitHub token: pass --token-file PATH, or set GITHUB_TOKEN_FILE "
                "or GITHUB_TOKEN"
            )
        client = GitHubClient(repo=args.repo, token=token)
        pull_requests = [
            client.get_pull_request(pr.number)
            for pr in client.list_open_pull_requests()
        ]
        integration_by_pr: dict[int, BranchIntegration] = {}
        branch_statuses: list[BranchStatus] = []
        for name in client.list_remote_branches():
            if name == DEFAULT_MAIN_REF:
                branch_statuses.append(BranchStatus(name=name, ahead_by=0, behind_by=0))
                continue
            details = client.compare_with_main_details(name)
            branch_statuses.append(
                BranchStatus(
                    name=name,
                    ahead_by=details.ahead_by,
                    behind_by=details.behind_by,
                )
            )
        for pr in pull_requests:
            if pr.head_repo != args.repo or pr.head_branch == DEFAULT_MAIN_REF:
                continue
            integration_by_pr[pr.number] = gather_branch_integration(
                client,
                pr.head_branch,
                pr_number=pr.number,
            )
        plan = build_plan(
            repo=args.repo,
            pull_requests=pull_requests,
            branches=branch_statuses,
            include_merged=args.include_merged,
            pr_numbers=set(args.pr) if args.pr is not None else None,
            integration_by_pr=integration_by_pr,
        )
        plan = replace(plan, dry_run=not args.apply)
        annotate_remaining = False
        if args.apply:
            annotate_remaining = not args.no_annotate_remaining
            if args.annotate_remaining:
                annotate_remaining = True
        if args.apply:
            operations = execute_plan(
                client,
                plan,
                max_operations=args.max,
                annotate_remaining=annotate_remaining,
            )
        else:
            operations = planned_operations(plan, max_operations=args.max)
    except (OSError, ValueError, urllib.error.URLError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2

    if args.json:
        output = asdict(plan)
        output["operations"] = operations
        print(json.dumps(output, ensure_ascii=False, indent=2))
    else:
        print(render_text(plan, operations))
    return 0


if __name__ == "__main__":
    sys.exit(main())
