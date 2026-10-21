"""Unit tests for scripts/worktree_orchestrator.py.

Everything runs against a throwaway git repository created in ``tmp_path``;
no network access and no touching of the developer's real checkout.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest

from scripts import worktree_orchestrator
from scripts.worktree_orchestrator import (
    STATE_ACTIVE,
    STATE_KEPT,
    STATE_RELEASED,
    WorktreeOrchestrator,
)

pytestmark = pytest.mark.skipif(
    shutil.which("git") is None, reason="git is required for worktree tests"
)


def _git(repo: Path, *args: str) -> str:
    proc = subprocess.run(
        ["git", *args],
        cwd=str(repo),
        capture_output=True,
        text=True,
        timeout=60,
        check=True,
    )
    return proc.stdout


@pytest.fixture()
def repo(tmp_path: Path) -> Path:
    """A minimal git repo with a single commit on ``main``."""
    root = tmp_path / "repo"
    root.mkdir()
    _git(root, "init")
    _git(root, "symbolic-ref", "HEAD", "refs/heads/main")
    _git(root, "config", "user.email", "agent@cursor.local")
    _git(root, "config", "user.name", "cursor-agent")
    (root / "README.md").write_text("worktree test repo\n", encoding="utf-8")
    _git(root, "add", "README.md")
    _git(root, "commit", "-m", "initial")
    return root


@pytest.fixture()
def orchestrator(repo: Path) -> WorktreeOrchestrator:
    return WorktreeOrchestrator(repo)


def _branches(repo: Path) -> list[str]:
    out = _git(repo, "for-each-ref", "--format=%(refname:short)", "refs/heads")
    return [line.strip() for line in out.splitlines() if line.strip()]


def test_create_makes_branch_and_worktree(
    repo: Path, orchestrator: WorktreeOrchestrator
) -> None:
    info = orchestrator.create("demo")

    assert info.branch == "cursor/demo"
    assert info.state == STATE_ACTIVE
    assert info.path == repo / ".worktrees" / "demo"
    assert info.path.is_dir()
    assert (info.path / "README.md").exists()
    assert "cursor/demo" in _branches(repo)

    registry = json.loads((repo / "data" / "worktrees.json").read_text(encoding="utf-8"))
    assert registry["version"] == 1
    assert [entry["slug"] for entry in registry["worktrees"]] == ["demo"]
    assert registry["worktrees"][0]["pid"] == os.getpid()


def test_create_with_variant_uses_variant_branch(
    repo: Path, orchestrator: WorktreeOrchestrator
) -> None:
    info = orchestrator.create("demo", variant=2)

    assert info.branch == "cursor/demo-variant-2"
    assert info.path.is_dir()
    assert orchestrator.path_for("demo", 2) == info.path


def test_create_is_idempotent(repo: Path, orchestrator: WorktreeOrchestrator) -> None:
    first = orchestrator.create("demo")
    second = orchestrator.create("demo")

    assert second.path == first.path
    assert second.branch == first.branch
    assert _branches(repo).count("cursor/demo") == 1
    assert len(orchestrator.list_all()) == 1


def test_create_restores_manually_deleted_worktree(
    repo: Path, orchestrator: WorktreeOrchestrator
) -> None:
    info = orchestrator.create("demo")
    shutil.rmtree(info.path)

    restored = orchestrator.create("demo")

    assert restored.path.is_dir()
    assert (restored.path / "README.md").exists()
    assert _branches(repo).count("cursor/demo") == 1


def test_release_removes_worktree_and_keeps_branch(
    repo: Path, orchestrator: WorktreeOrchestrator
) -> None:
    info = orchestrator.create("demo")

    assert orchestrator.release("demo") is True
    assert not info.path.exists()
    # Unpushed agent work must survive: the branch stays unless asked otherwise.
    assert "cursor/demo" in _branches(repo)
    assert orchestrator.get("demo").state == STATE_RELEASED


def test_release_with_keep_leaves_directory(
    repo: Path, orchestrator: WorktreeOrchestrator
) -> None:
    info = orchestrator.create("demo")

    assert orchestrator.release("demo", keep=True) is True
    assert info.path.is_dir()
    assert orchestrator.get("demo").state == STATE_KEPT


def test_release_with_delete_branch_removes_branch(
    repo: Path, orchestrator: WorktreeOrchestrator
) -> None:
    orchestrator.create("demo")

    assert orchestrator.release("demo", delete_branch=True) is True
    assert "cursor/demo" not in _branches(repo)


def test_release_unknown_slug_returns_false(
    orchestrator: WorktreeOrchestrator,
) -> None:
    assert orchestrator.release("never-created") is False


def test_release_failure_marks_entry_as_error(
    monkeypatch: pytest.MonkeyPatch, orchestrator: WorktreeOrchestrator
) -> None:
    info = orchestrator.create("demo")
    real_run_git = orchestrator._run_git

    def fake_run_git(args, *, check=True):
        if args[:2] == ["worktree", "remove"]:
            raise worktree_orchestrator.WorktreeError("worktree is locked")
        return real_run_git(args, check=check)

    monkeypatch.setattr(orchestrator, "_run_git", fake_run_git)

    assert orchestrator.release("demo") is False
    assert info.path.is_dir()
    stored = orchestrator.get("demo")
    assert stored.state == "error"
    assert "locked" in stored.error


def test_reap_releases_dead_owner_and_skips_kept(
    monkeypatch: pytest.MonkeyPatch, orchestrator: WorktreeOrchestrator
) -> None:
    dead = orchestrator.create("dead-agent")
    kept = orchestrator.create("kept-agent")
    alive = orchestrator.create("alive-agent")
    orchestrator.release("kept-agent", keep=True)
    orchestrator.update("dead-agent", pid=424242)

    monkeypatch.setattr(
        worktree_orchestrator, "_pid_alive", lambda pid: pid != 424242
    )

    reaped = orchestrator.reap(older_than_hours=24)

    assert [info.slug for info in reaped] == ["dead-agent"]
    assert not dead.path.exists()
    assert kept.path.is_dir()
    assert alive.path.is_dir()
    assert orchestrator.get("kept-agent").state == STATE_KEPT
    assert orchestrator.get("alive-agent").state == STATE_ACTIVE


def test_reap_releases_entries_older_than_threshold(
    orchestrator: WorktreeOrchestrator,
) -> None:
    info = orchestrator.create("stale")
    orchestrator.update("stale", created_at="2000-01-01T00:00:00+00:00")

    reaped = orchestrator.reap(older_than_hours=1)

    assert [entry.slug for entry in reaped] == ["stale"]
    assert not info.path.exists()


def test_list_all_flags_missing_on_disk(orchestrator: WorktreeOrchestrator) -> None:
    info = orchestrator.create("demo")
    shutil.rmtree(info.path)

    entries = {entry.slug: entry for entry in orchestrator.list_all()}

    assert entries["demo"].missing_on_disk is True
    assert entries["demo"].not_in_git is False


def test_list_all_flags_unregistered_worktree(
    repo: Path, orchestrator: WorktreeOrchestrator
) -> None:
    manual = repo / ".worktrees" / "manual"
    _git(repo, "worktree", "add", str(manual), "-b", "cursor/manual", "main")

    entries = {entry.slug: entry for entry in orchestrator.list_all()}

    assert entries["manual"].not_in_registry is True
    assert entries["manual"].branch == "cursor/manual"


def test_doctor_reports_no_issues_on_clean_repo(
    orchestrator: WorktreeOrchestrator,
) -> None:
    report = orchestrator.doctor()

    assert report["issues"] == []
    assert report["entries"] == []


def test_doctor_reports_missing_worktree(orchestrator: WorktreeOrchestrator) -> None:
    info = orchestrator.create("demo")
    shutil.rmtree(info.path)

    kinds = {issue["kind"] for issue in orchestrator.doctor()["issues"]}

    assert "missing_on_disk" in kinds


def test_update_records_agent_metadata(orchestrator: WorktreeOrchestrator) -> None:
    orchestrator.create("demo")

    orchestrator.update(
        "demo",
        agent_id="agent-123",
        task_slug="task-12",
        pr_url="https://github.com/o/r/pull/1",
    )

    stored = orchestrator.get("demo")
    assert stored.agent_id == "agent-123"
    assert stored.task_slug == "task-12"
    assert stored.pr_url == "https://github.com/o/r/pull/1"


def test_registry_write_is_atomic_and_leaves_no_temp_files(
    repo: Path, orchestrator: WorktreeOrchestrator
) -> None:
    orchestrator.create("demo")

    data_dir = repo / "data"
    assert [p.name for p in data_dir.iterdir()] == ["worktrees.json"]


def test_cli_create_list_release_round_trip(
    repo: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    base_args = ["--repo", str(repo)]

    assert worktree_orchestrator.main(base_args + ["create", "--slug", "demo"]) == 0
    assert "cursor/demo" in capsys.readouterr().out

    assert worktree_orchestrator.main(base_args + ["list"]) == 0
    assert "demo" in capsys.readouterr().out

    assert (
        worktree_orchestrator.main(
            base_args + ["release", "--slug", "demo", "--keep"]
        )
        == 0
    )
    assert "state=kept" in capsys.readouterr().out
    assert (repo / ".worktrees" / "demo").is_dir()

    assert worktree_orchestrator.main(base_args + ["doctor"]) in (0, 1)


def test_normalize_slug_strips_prefixes_and_separators() -> None:
    assert worktree_orchestrator.normalize_slug("origin/cursor/fix/thing") == "fix-thing"
    assert worktree_orchestrator.normalize_slug("cursor/demo") == "demo"
    with pytest.raises(ValueError):
        worktree_orchestrator.normalize_slug("///")
