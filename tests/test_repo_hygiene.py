"""Tests for the GitHub repository hygiene CLI."""

from __future__ import annotations

import json
from pathlib import Path
import subprocess

import pytest

from scripts import repo_hygiene
from scripts.repo_hygiene import (
    BranchIntegration,
    BranchStatus,
    BranchDecision,
    GitHubClient,
    HygienePlan,
    PullRequest,
    build_plan,
    compare_ahead_by,
    compare_behind_by,
    execute_plan,
    git_cherry_counts,
    git_paths_missing_on_main,
)


def test_compare_ahead_by_zero_means_fully_merged() -> None:
    """A branch with no commits ahead of main is safe to delete."""
    assert compare_ahead_by({"status": "identical", "ahead_by": 0}) == 0


def test_compare_ahead_by_preserves_unmerged_commit_count() -> None:
    """A positive ahead_by value identifies a branch with unmerged work."""
    assert compare_ahead_by({"status": "ahead", "ahead_by": 3}) == 3


def test_compare_behind_by_counts_main_only_commits() -> None:
    """Behind count captures how stale a feature branch is."""
    assert compare_behind_by({"status": "behind", "behind_by": 120}) == 120


@pytest.mark.parametrize("payload", [{}, {"ahead_by": -1}, {"ahead_by": "0"}])
def test_compare_ahead_by_rejects_invalid_payloads(payload: dict[str, object]) -> None:
    """Malformed compare responses must not be treated as merged branches."""
    with pytest.raises(ValueError):
        compare_ahead_by(payload)


def _branch(name: str, ahead_by: int) -> BranchStatus:
    return BranchStatus(name=name, ahead_by=ahead_by)


def _pr(number: int, branch: str) -> PullRequest:
    return PullRequest(
        number=number,
        title=f"Pull request {number}",
        head_branch=branch,
        head_repo="owner/repo",
    )


def test_dry_run_plan_deletes_only_merged_branches_when_enabled() -> None:
    """Merged branches are candidates; unmerged work is always retained."""
    plan = build_plan(
        repo="owner/repo",
        pull_requests=[],
        branches=[_branch("main", 0), _branch("merged", 0), _branch("work", 2)],
        include_merged=True,
    )

    decisions = {branch.name: branch for branch in plan.branches}
    assert decisions["merged"].action == "delete"
    assert decisions["work"].action == "keep"
    assert decisions["work"].ahead_by == 2


def test_main_is_never_a_delete_candidate() -> None:
    """The default branch remains protected even when merged cleanup is enabled."""
    plan = build_plan(
        repo="owner/repo",
        pull_requests=[],
        branches=[_branch("main", 0)],
        include_merged=True,
    )

    assert plan.branches[0].action == "keep"
    assert "protected" in plan.branches[0].reason


def test_open_pr_protects_its_head_branch_from_deletion() -> None:
    """A merged branch is retained while its pull request is still open."""
    plan = build_plan(
        repo="owner/repo",
        pull_requests=[_pr(7, "merged")],
        branches=[_branch("merged", 0)],
        include_merged=True,
    )

    assert plan.branches[0].action == "keep"
    assert plan.branches[0].open_prs == [7]
    assert "open PR #7" in plan.branches[0].reason


def test_merged_branches_are_not_deleted_without_include_merged() -> None:
    """Branch cleanup requires an explicit opt-in."""
    plan = build_plan(
        repo="owner/repo",
        pull_requests=[],
        branches=[_branch("merged", 0)],
        include_merged=False,
    )

    assert plan.branches[0].action == "keep"
    assert "--include-merged" in plan.branches[0].reason


def test_dry_run_plan_closes_only_prs_whose_head_is_merged() -> None:
    """A fully merged PR can be closed, while one with unique commits remains open."""
    plan = build_plan(
        repo="owner/repo",
        pull_requests=[_pr(7, "merged"), _pr(8, "work")],
        branches=[_branch("merged", 0), _branch("work", 1)],
        include_merged=False,
    )

    decisions = {pr.number: pr for pr in plan.pull_requests}
    assert decisions[7].action == "close"
    assert decisions[8].action == "keep"
    assert plan.dry_run is True


def test_patch_equivalent_via_git_cherry_closes_even_when_ahead_by_positive() -> None:
    """Squash/cherry-pick merges keep ahead_by>0 but show no unique cherry commits."""
    plan = build_plan(
        repo="owner/repo",
        pull_requests=[_pr(271, "feat/video-pipeline-runner")],
        branches=[_branch("feat/video-pipeline-runner", 1)],
        integration_by_pr={
            271: BranchIntegration(
                behind_by=320,
                cherry_equivalent=1,
                cherry_unique=0,
                main_integration_commit="abfe2f0 feat: declarative video pipeline runner",
            )
        },
    )
    decision = plan.pull_requests[0]
    assert decision.action == "close"
    assert "cherry-pick" in decision.reason
    assert "abfe2f0" in decision.comment_body


def test_paths_missing_on_main_keep_pull_request_open() -> None:
    """When unique files never landed on main, the PR must remain open."""
    plan = build_plan(
        repo="owner/repo",
        pull_requests=[
            PullRequest(
                number=249,
                title="daily_brief",
                head_branch="daily-brief-gcal-gmail-telegram",
                head_repo="owner/repo",
                mergeable="CONFLICTING",
                merge_state_status="UNKNOWN",
            )
        ],
        branches=[_branch("daily-brief-gcal-gmail-telegram", 3)],
        integration_by_pr={
            249: BranchIntegration(
                behind_by=341,
                paths_missing_on_main=(
                    "scripts/daily_brief.py",
                    "tasks/daily_brief.yaml",
                ),
            )
        },
    )
    decision = plan.pull_requests[0]
    assert decision.action == "keep"
    assert "scripts/daily_brief.py" in decision.reason
    assert "оставляю открытым" in decision.comment_body


def test_stale_superseded_branch_closes_when_main_caught_up() -> None:
    """Old drafts with no missing paths and large behind counts are closed."""
    plan = build_plan(
        repo="owner/repo",
        pull_requests=[_pr(120, "self-improvement/auto-reflection")],
        branches=[_branch("self-improvement/auto-reflection", 1)],
        integration_by_pr={
            120: BranchIntegration(
                behind_by=375,
                cherry_equivalent=0,
                cherry_unique=1,
                paths_missing_on_main=(),
                main_integration_commit="465e75e Enhance auto_reflection",
            )
        },
    )
    decision = plan.pull_requests[0]
    assert decision.action == "close"
    assert "superseded" in decision.reason


def _git_in(repo: Path, *args: str) -> None:
    subprocess.run(
        ["git", *args],
        cwd=repo,
        check=True,
        capture_output=True,
        text=True,
    )


def _init_squash_cherry_repo(tmp_path: Path) -> Path:
    """Feature branch with one commit whose patch already exists on main (squash/cherry)."""
    repo = tmp_path / "squash-cherry"
    repo.mkdir()
    _git_in(repo, "init", "-b", "main")
    _git_in(repo, "config", "user.email", "hygiene@example.com")
    _git_in(repo, "config", "user.name", "hygiene")
    (repo / "README.md").write_text("base\n", encoding="utf-8")
    _git_in(repo, "add", "README.md")
    _git_in(repo, "commit", "-m", "base on main")
    _git_in(repo, "branch", "feat/video-pipeline-runner")
    _git_in(repo, "checkout", "feat/video-pipeline-runner")
    (repo / "video_pipeline").mkdir()
    (repo / "video_pipeline" / "runner.py").write_text("print('pipeline')\n", encoding="utf-8")
    _git_in(repo, "add", "video_pipeline/runner.py")
    _git_in(repo, "commit", "-m", "feat: declarative video pipeline runner")
    _git_in(repo, "checkout", "main")
    _git_in(repo, "merge", "--squash", "feat/video-pipeline-runner")
    _git_in(repo, "commit", "-m", "squash on main")
    return repo


def _init_daily_brief_repo(tmp_path: Path) -> Path:
    """Branch introduces new paths that main never received."""
    repo = tmp_path / "daily-brief"
    repo.mkdir()
    _git_in(repo, "init", "-b", "main")
    _git_in(repo, "config", "user.email", "hygiene@example.com")
    _git_in(repo, "config", "user.name", "hygiene")
    (repo / "README.md").write_text("base\n", encoding="utf-8")
    _git_in(repo, "add", "README.md")
    _git_in(repo, "commit", "-m", "base on main")
    branch = "daily-brief-gcal-gmail-telegram"
    _git_in(repo, "checkout", "-b", branch)
    (repo / "scripts").mkdir()
    (repo / "scripts" / "daily_brief.py").write_text("# brief\n", encoding="utf-8")
    (repo / "scripts" / "health_sheets.py").write_text("# health\n", encoding="utf-8")
    (repo / "tasks").mkdir()
    (repo / "tasks" / "daily_brief.yaml").write_text("task: brief\n", encoding="utf-8")
    (repo / "tasks" / "health_sheets.yaml").write_text("task: health\n", encoding="utf-8")
    _git_in(repo, "add", ".")
    _git_in(repo, "commit", "-m", "daily_brief integration")
    _git_in(repo, "checkout", "main")
    return repo


def test_git_cherry_counts_sees_patch_equivalent_commit(tmp_path: Path) -> None:
    """Squash/cherry integration: branch commit is patch-present on main (git cherry -)."""
    repo = _init_squash_cherry_repo(tmp_path)
    counts = git_cherry_counts(
        "feat/video-pipeline-runner",
        cwd=repo,
        main_ref="main",
        head_ref="feat/video-pipeline-runner",
    )
    assert counts == (1, 0)


def test_git_cherry_counts_sees_unique_commit(tmp_path: Path) -> None:
    """Unique branch work shows as git cherry + when main lacks the patch."""
    repo = _init_daily_brief_repo(tmp_path)
    counts = git_cherry_counts(
        "daily-brief-gcal-gmail-telegram",
        cwd=repo,
        main_ref="main",
        head_ref="daily-brief-gcal-gmail-telegram",
    )
    assert counts == (0, 1)


def test_git_paths_missing_on_main_lists_daily_brief_paths(tmp_path: Path) -> None:
    """All paths newly added on the branch are absent from main."""
    repo = _init_daily_brief_repo(tmp_path)
    missing = git_paths_missing_on_main(
        "daily-brief-gcal-gmail-telegram",
        cwd=repo,
        main_ref="main",
        head_ref="daily-brief-gcal-gmail-telegram",
    )
    assert "scripts/daily_brief.py" in missing
    assert "scripts/health_sheets.py" in missing
    assert "tasks/daily_brief.yaml" in missing
    assert "tasks/health_sheets.yaml" in missing
    assert len(missing) == 4


def test_git_paths_missing_on_main_skips_paths_present_on_main(tmp_path: Path) -> None:
    """Paths that already exist on main are not treated as missing."""
    repo = tmp_path / "partial-land"
    repo.mkdir()
    _git_in(repo, "init", "-b", "main")
    _git_in(repo, "config", "user.email", "hygiene@example.com")
    _git_in(repo, "config", "user.name", "hygiene")
    (repo / "README.md").write_text("base\n", encoding="utf-8")
    _git_in(repo, "add", "README.md")
    _git_in(repo, "commit", "-m", "base on main")
    _git_in(repo, "checkout", "-b", "feature")
    (repo / "scripts").mkdir()
    (repo / "scripts" / "daily_brief.py").write_text("# brief\n", encoding="utf-8")
    (repo / "scripts" / "only_on_branch.py").write_text("# unique\n", encoding="utf-8")
    _git_in(repo, "add", ".")
    _git_in(repo, "commit", "-m", "feature branch")
    _git_in(repo, "checkout", "main")
    (repo / "scripts").mkdir(exist_ok=True)
    (repo / "scripts" / "daily_brief.py").write_text("# brief\n", encoding="utf-8")
    _git_in(repo, "add", "scripts/daily_brief.py")
    _git_in(repo, "commit", "-m", "land daily_brief on main")
    missing = git_paths_missing_on_main(
        "feature",
        cwd=repo,
        main_ref="main",
        head_ref="feature",
    )
    assert missing == ["scripts/only_on_branch.py"]


class RecordingClient(GitHubClient):
    """A GitHub client double that records requests instead of using the network."""

    def __init__(self) -> None:
        super().__init__(repo="owner/repo", token="not-a-real-token")
        self.calls: list[tuple[str, str, dict[str, object] | None]] = []
        self.responses: list[object] = []

    def _request(
        self,
        method: str,
        path: str,
        payload: dict[str, object] | None = None,
    ) -> object:
        self.calls.append((method, path, payload))
        if self.responses:
            return self.responses.pop(0)
        return {}


def test_apply_uses_expected_patch_and_delete_paths() -> None:
    """Applying a plan closes merged PRs and deletes merged branches via REST."""
    client = RecordingClient()
    plan = build_plan(
        repo="owner/repo",
        pull_requests=[_pr(7, "merged-pr")],
        branches=[_branch("merged-pr", 0), _branch("feature/old", 0)],
        include_merged=True,
    )

    execute_plan(client, plan)

    assert client.calls == [
        (
            "POST",
            "/repos/owner/repo/issues/7/comments",
            {"body": "Закрываю PR #7: head-ветка полностью достижима из `main` (ahead_by=0)."},
        ),
        (
            "PATCH",
            "/repos/owner/repo/pulls/7",
            {"state": "closed"},
        ),
        (
            "DELETE",
            "/repos/owner/repo/git/refs/heads/feature/old",
            None,
        ),
    ]


def test_apply_max_limits_mutating_operations() -> None:
    """The operation cap applies to the combined PR and branch cleanup."""
    client = RecordingClient()
    plan = build_plan(
        repo="owner/repo",
        pull_requests=[_pr(7, "merged-pr")],
        branches=[_branch("merged-pr", 0), _branch("feature/old", 0)],
        include_merged=True,
    )

    execute_plan(client, plan, max_operations=2)

    assert len(client.calls) == 2
    assert client.calls[0][0] == "POST"
    assert client.calls[1][0] == "PATCH"


def test_execute_plan_refuses_a_malformed_main_delete() -> None:
    """The execution layer protects main even if handed an invalid plan."""
    client = RecordingClient()
    unsafe_plan = HygienePlan(
        repo="owner/repo",
        dry_run=False,
        pull_requests=[],
        branches=[
            # Construct an invalid decision to verify defense in depth.
            BranchDecision(
                name="main",
                ahead_by=0,
                merged=True,
                open_prs=[],
                action="delete",
                reason="invalid test fixture",
            )
        ],
    )

    execute_plan(client, unsafe_plan)

    assert client.calls == []


def test_github_client_lists_and_compares_without_embedding_credentials() -> None:
    """REST helpers parse API responses and keep branch slashes in compare paths."""
    client = RecordingClient()
    client.responses = [
        [
            {
                "number": 7,
                "title": "Merged change",
                "draft": False,
                "mergeable": True,
                "merge_state_status": "CLEAN",
                "head": {
                    "ref": "feature/old",
                    "repo": {"full_name": "owner/repo"},
                },
            }
        ],
        [{"name": "main"}, {"name": "feature/old"}],
        {"ahead_by": 0, "behind_by": 0},
    ]

    pull_requests = client.list_open_pull_requests()
    branches = client.list_remote_branches()
    ahead_by = client.compare_with_main("feature/old")

    assert len(pull_requests) == 1
    assert pull_requests[0].number == 7
    assert pull_requests[0].head_branch == "feature/old"
    assert pull_requests[0].head_repo == "owner/repo"
    assert pull_requests[0].title == "Merged change"
    assert branches == ["main", "feature/old"]
    assert ahead_by == 0
    assert client.calls[-1] == (
        "GET",
        "/repos/owner/repo/compare/main...feature/old",
        None,
    )
    assert all("not-a-real-token" not in path for _, path, _ in client.calls)


class FakeClient(RecordingClient):
    """A complete client fixture for exercising the CLI without network access."""

    def __init__(
        self,
        pull_requests: list[PullRequest],
        branches: dict[str, int],
    ) -> None:
        super().__init__()
        self.pull_requests = pull_requests
        self.branch_ahead = branches

    def list_open_pull_requests(self) -> list[PullRequest]:
        return self.pull_requests

    def list_remote_branches(self) -> list[str]:
        return list(self.branch_ahead)

    def compare_with_main(self, branch: str) -> int:
        return self.branch_ahead[branch]

    def compare_with_main_details(self, branch: str) -> BranchStatus:
        ahead = self.branch_ahead[branch]
        return BranchStatus(name=branch, ahead_by=ahead, behind_by=0)

    def get_pull_request(self, number: int) -> PullRequest:
        for pr in self.pull_requests:
            if pr.number == number:
                return pr
        raise ValueError(f"missing pull request #{number}")

    def paths_added_by_branch_missing_on_main(self, branch: str) -> list[str]:
        return []

    def file_exists_on_main(self, path: str) -> bool:
        return True


def _token_file(tmp_path: Path) -> Path:
    path = tmp_path / "github-token"
    path.write_text("super-secret-token\n", encoding="utf-8")
    return path


def _install_client(monkeypatch: pytest.MonkeyPatch, client: FakeClient) -> None:
    monkeypatch.setattr(repo_hygiene, "GitHubClient", lambda **kwargs: client)


def test_cli_defaults_to_dry_run_and_never_prints_token(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Without --apply the CLI prints a plan and performs no mutations."""
    client = FakeClient(
        [_pr(7, "merged-pr")],
        {"main": 0, "merged-pr": 0, "feature/old": 0},
    )
    monkeypatch.setattr(
        repo_hygiene,
        "gather_branch_integration",
        lambda *_args, **_kwargs: BranchIntegration(),
    )
    _install_client(monkeypatch, client)

    exit_code = repo_hygiene.main(
        [
            "--repo",
            "owner/repo",
            "--token-file",
            str(_token_file(tmp_path)),
            "--include-merged",
        ]
    )

    captured = capsys.readouterr()
    assert exit_code == 0
    assert "DRY RUN" in captured.out
    assert "CLOSE #7" in captured.out
    assert "DELETE feature/old" in captured.out
    assert "KEEP main" in captured.out
    assert client.calls == []
    assert "super-secret-token" not in captured.out + captured.err


def test_cli_apply_performs_only_planned_mutations(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """The explicit --apply flag enables PATCH and DELETE requests."""
    client = FakeClient(
        [_pr(7, "merged-pr")],
        {"main": 0, "merged-pr": 0, "feature/old": 0},
    )
    _install_client(monkeypatch, client)
    monkeypatch.setattr(
        repo_hygiene,
        "gather_branch_integration",
        lambda *_args, **_kwargs: BranchIntegration(),
    )

    exit_code = repo_hygiene.main(
        [
            "--repo",
            "owner/repo",
            "--token-file",
            str(_token_file(tmp_path)),
            "--include-merged",
            "--apply",
        ]
    )

    assert exit_code == 0
    assert [call[:2] for call in client.calls] == [
        ("POST", "/repos/owner/repo/issues/7/comments"),
        ("PATCH", "/repos/owner/repo/pulls/7"),
        ("DELETE", "/repos/owner/repo/git/refs/heads/feature/old"),
    ]
    assert "APPLY" in capsys.readouterr().out


def test_cli_json_and_pr_filter_keep_unselected_prs_as_branch_protection(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """PR filtering limits actions, not the safety data used for branch decisions."""
    client = FakeClient(
        [_pr(7, "merged-pr"), _pr(8, "work")],
        {"main": 0, "merged-pr": 0, "work": 0},
    )
    _install_client(monkeypatch, client)
    monkeypatch.setattr(
        repo_hygiene,
        "gather_branch_integration",
        lambda *_args, **_kwargs: BranchIntegration(),
    )

    exit_code = repo_hygiene.main(
        [
            "--repo",
            "owner/repo",
            "--token-file",
            str(_token_file(tmp_path)),
            "--include-merged",
            "--pr",
            "7",
            "--json",
        ]
    )

    payload = json.loads(capsys.readouterr().out)
    assert exit_code == 0
    assert payload["dry_run"] is True
    assert [pr["number"] for pr in payload["pull_requests"]] == [7]
    branches = {branch["name"]: branch for branch in payload["branches"]}
    assert branches["work"]["action"] == "keep"
    assert branches["work"]["open_prs"] == [8]
    assert client.calls == []
