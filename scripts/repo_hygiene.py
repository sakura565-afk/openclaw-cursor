#!/usr/bin/env python3
"""Inspect and clean up merged GitHub pull requests and remote branches."""

from __future__ import annotations

import argparse
from collections.abc import Mapping, Sequence
from dataclasses import asdict, dataclass, replace
import json
from pathlib import Path
import sys
from typing import Any, Literal
import urllib.error
import urllib.request
from urllib.parse import quote


DEFAULT_REPO = "sakura565-afk/openclaw-cursor"
DEFAULT_TOKEN_FILE = Path(
    "C:/Users/user/AppData/Local/hermes/github_token.txt"
)


Action = Literal["close", "delete", "keep"]


@dataclass(frozen=True)
class PullRequest:
    """The pull-request fields needed to make a cleanup decision."""

    number: int
    title: str
    head_branch: str
    head_repo: str


@dataclass(frozen=True)
class BranchStatus:
    """A remote branch and its commit count relative to main."""

    name: str
    ahead_by: int


@dataclass(frozen=True)
class PullRequestDecision:
    """The planned action for an open pull request."""

    number: int
    title: str
    head_branch: str
    action: Action
    reason: str


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
            pull_requests.append(
                PullRequest(
                    number=number,
                    title=title,
                    head_branch=branch,
                    head_repo=repo_name,
                )
            )
        return pull_requests

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
        encoded_branch = quote(branch, safe="/")
        payload = self._request(
            "GET",
            f"/repos/{self.repo}/compare/main...{encoded_branch}",
        )
        if not isinstance(payload, Mapping):
            raise ValueError("GitHub compare endpoint returned a non-object response")
        return compare_ahead_by(payload)

    def close_pull_request(self, number: int) -> None:
        """Close an open pull request."""
        self._request(
            "PATCH",
            f"/repos/{self.repo}/pulls/{number}",
            {"state": "closed"},
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


def _classify_pull_request(
    pull_request: PullRequest,
    branches: Mapping[str, BranchStatus],
    repo: str,
) -> PullRequestDecision:
    """Close only pull requests whose same-repository head is fully merged."""
    branch = branches.get(pull_request.head_branch)
    if pull_request.head_repo != repo:
        action: Action = "keep"
        reason = "head branch belongs to a fork"
    elif pull_request.head_branch == "main":
        action = "keep"
        reason = "head branch is the protected default branch"
    elif branch is None:
        action = "keep"
        reason = "head branch was not found among remote branches"
    elif branch.ahead_by == 0:
        action = "close"
        reason = "head branch is fully merged with main (ahead_by=0)"
    else:
        action = "keep"
        reason = f"head branch has {branch.ahead_by} commit(s) not in main"
    return PullRequestDecision(
        number=pull_request.number,
        title=pull_request.title,
        head_branch=pull_request.head_branch,
        action=action,
        reason=reason,
    )


def build_plan(
    *,
    repo: str,
    pull_requests: Sequence[PullRequest],
    branches: Sequence[BranchStatus],
    include_merged: bool = False,
    pr_numbers: set[int] | None = None,
) -> HygienePlan:
    """Build a dry-run plan from open pull requests and compared branches."""
    branch_by_name = {branch.name: branch for branch in branches}
    pr_decisions = [
        _classify_pull_request(pr, branch_by_name, repo)
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
) -> list[str]:
    """Apply the mutating actions in a plan and return a concise audit list."""
    if max_operations is not None and max_operations < 0:
        raise ValueError("max_operations must be non-negative")
    operations: list[str] = []
    for pr in plan.pull_requests:
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
        default=DEFAULT_TOKEN_FILE,
        help="file containing a GitHub token",
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
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    """Run the repository hygiene CLI."""
    args = build_parser().parse_args(argv)
    try:
        token = args.token_file.read_text(encoding="utf-8-sig").strip()
        if not token:
            raise ValueError(f"token file is empty: {args.token_file}")
        client = GitHubClient(repo=args.repo, token=token)
        pull_requests = client.list_open_pull_requests()
        branch_statuses = [
            BranchStatus(name=name, ahead_by=0)
            if name == "main"
            else BranchStatus(name=name, ahead_by=client.compare_with_main(name))
            for name in client.list_remote_branches()
        ]
        plan = build_plan(
            repo=args.repo,
            pull_requests=pull_requests,
            branches=branch_statuses,
            include_merged=args.include_merged,
            pr_numbers=set(args.pr) if args.pr is not None else None,
        )
        plan = replace(plan, dry_run=not args.apply)
        if args.apply:
            operations = execute_plan(client, plan, max_operations=args.max)
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
