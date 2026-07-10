#!/usr/bin/env python3
"""Git-based version control for ComfyUI workflow JSONs.

Wraps ``git`` with metadata-rich commits (test result, seed, model hash,
tester) and provides node-level diffing of ComfyUI workflow graphs. Designed
for the ComfyUI workflows kept under ``comfyui_workflows/`` on the local
Windows install; only performs local commits (never pushes).
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Literal, Optional

WORKFLOWS_DIR = Path("comfyui_workflows")

GIT_USER_NAME = "cursor-agent"
GIT_USER_EMAIL = "agent@cursor.local"

META_SUBDIR = ".meta"

TestResult = Literal["approved", "rejected", "untested"]
DiffRef = Literal["HEAD", "HEAD~1", "main"]

VALID_TEST_RESULTS = ("approved", "rejected", "untested")


class WorkflowVCSError(RuntimeError):
    """Raised when a git operation or workflow lookup fails."""


@dataclass
class NodeChange:
    """A single node added/removed/modified between two workflow revisions."""

    id: str
    class_type: str


@dataclass
class CommitResult:
    commit_sha: str
    files_changed: list[str]
    diff_summary: str
    metadata: dict[str, Any]


@dataclass
class DiffResult:
    old_hash: Optional[str]
    new_hash: Optional[str]
    nodes_added: list[dict[str, str]] = field(default_factory=list)
    nodes_removed: list[dict[str, str]] = field(default_factory=list)
    nodes_modified: list[dict[str, str]] = field(default_factory=list)
    raw_diff: str = ""


@dataclass
class CommitInfo:
    sha: str
    date: str
    subject: str
    metadata: dict[str, str] = field(default_factory=dict)


class WorkflowVCS:
    """Version-control wrapper around a directory of ComfyUI workflow JSONs."""

    def __init__(self, workflows_dir: Path, git_dir: Path | None = None) -> None:
        self.workflows_dir = Path(workflows_dir)
        # ``git_dir`` lets tests/callers keep the ``.git`` metadata somewhere
        # other than the working tree. When omitted, the workflows dir is the
        # repository root (the common case).
        self.git_dir = Path(git_dir) if git_dir is not None else self.workflows_dir

    # -- git plumbing -----------------------------------------------------

    def _git_env(self) -> dict[str, str]:
        env = os.environ.copy()
        if self.git_dir != self.workflows_dir:
            env["GIT_DIR"] = str(self.git_dir)
            env["GIT_WORK_TREE"] = str(self.workflows_dir)
        return env

    def _run_git(
        self, args: list[str], *, check: bool = True
    ) -> subprocess.CompletedProcess[str]:
        proc = subprocess.run(
            ["git", *args],
            cwd=str(self.workflows_dir),
            env=self._git_env(),
            capture_output=True,
            text=True,
        )
        if check and proc.returncode != 0:
            raise WorkflowVCSError(
                f"git {' '.join(args)} failed ({proc.returncode}): "
                f"{proc.stderr.strip() or proc.stdout.strip()}"
            )
        return proc

    def _is_repo(self) -> bool:
        """True only when a repo is rooted at ``workflows_dir`` itself.

        This deliberately does not treat "inside a parent repo" as initialised,
        so that a workflows dir nested within another git repo still gets its
        own dedicated repository on init().
        """
        if self.git_dir != self.workflows_dir:
            return (self.git_dir / "HEAD").exists()
        proc = self._run_git(["rev-parse", "--show-toplevel"], check=False)
        if proc.returncode != 0 or not proc.stdout.strip():
            return False
        return Path(proc.stdout.strip()).resolve() == self.workflows_dir.resolve()

    def _has_commits(self) -> bool:
        return self._run_git(["rev-parse", "--verify", "HEAD"], check=False).returncode == 0

    def _rel(self, workflow_path: Path) -> str:
        """Return the workflow path relative to the working tree, posix-style."""
        p = Path(workflow_path)
        if p.is_absolute():
            try:
                p = p.relative_to(self.workflows_dir)
            except ValueError as exc:  # pragma: no cover - defensive
                raise WorkflowVCSError(
                    f"{workflow_path} is not inside {self.workflows_dir}"
                ) from exc
        # A bare filename is already relative to the working tree.
        return p.as_posix()

    # -- public API -------------------------------------------------------

    def init(self) -> None:
        """Initialise the git repo (idempotent) and write a ``.gitignore``."""
        self.workflows_dir.mkdir(parents=True, exist_ok=True)
        if not self._is_repo():
            # ``-b main`` keeps the default branch name deterministic across
            # git versions so ``--against main`` behaves predictably.
            init = self._run_git(["init", "-b", "main"], check=False)
            if init.returncode != 0:
                # Older git without ``-b``: fall back and rename.
                self._run_git(["init"])
                self._run_git(["symbolic-ref", "HEAD", "refs/heads/main"], check=False)

        self._ensure_identity()

        gitignore = self.workflows_dir / ".gitignore"
        if not gitignore.exists():
            gitignore.write_text(
                "*.bak\n*.tmp\n__pycache__/\n", encoding="utf-8"
            )

    def _ensure_identity(self) -> None:
        """Ensure a commit identity exists (repo-local, does not touch global)."""
        for key, value in (("user.name", GIT_USER_NAME), ("user.email", GIT_USER_EMAIL)):
            current = self._run_git(["config", key], check=False)
            if current.returncode != 0 or not current.stdout.strip():
                self._run_git(["config", key, value])

    def commit(
        self,
        workflow_path: Path,
        *,
        message: str,
        seed: int | None = None,
        model_hash: str | None = None,
        test_result: TestResult = "untested",
        tester: str = "cursor-agent",
    ) -> CommitResult:
        """Stage and commit a workflow JSON with structured test metadata."""
        if test_result not in VALID_TEST_RESULTS:
            raise WorkflowVCSError(
                f"test_result must be one of {VALID_TEST_RESULTS}, got {test_result!r}"
            )
        if not self._is_repo():
            raise WorkflowVCSError(
                f"{self.workflows_dir} is not a git repo; run init() first"
            )

        rel = self._rel(workflow_path)
        target = self.workflows_dir / rel
        if not target.exists():
            raise WorkflowVCSError(f"workflow not found: {target}")

        workflow_name = target.name
        commit_message = self._build_message(
            workflow_name,
            message,
            test_result=test_result,
            seed=seed,
            model_hash=model_hash,
            tester=tester,
        )

        self._run_git(["add", "--", rel])
        # ``--allow-empty`` records re-tests even when the JSON is unchanged, so
        # the metadata history stays complete and the call is idempotent-safe.
        self._run_git(["commit", "--allow-empty", "-m", commit_message])

        commit_sha = self._run_git(["rev-parse", "HEAD"]).stdout.strip()
        files_changed = self._files_changed(commit_sha)
        diff_summary = self._run_git(
            ["show", "--stat", "--format=", commit_sha]
        ).stdout.strip()

        metadata: dict[str, Any] = {
            "workflow": workflow_name,
            "message": message,
            "test_result": test_result,
            "seed": seed,
            "model_hash": model_hash,
            "tester": tester,
            "commit_sha": commit_sha,
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }
        self.sync_meta_template(target, metadata)

        return CommitResult(
            commit_sha=commit_sha,
            files_changed=files_changed,
            diff_summary=diff_summary,
            metadata=metadata,
        )

    @staticmethod
    def _build_message(
        workflow_name: str,
        message: str,
        *,
        test_result: str,
        seed: int | None,
        model_hash: str | None,
        tester: str,
    ) -> str:
        return (
            f"{workflow_name}: {message}\n"
            f"\n"
            f"Test result: {test_result}\n"
            f"Seed: {seed if seed is not None else 'none'}\n"
            f"Model hash: {model_hash if model_hash else 'none'}\n"
            f"Tester: {tester}"
        )

    def _files_changed(self, commit_sha: str) -> list[str]:
        # ``--root`` makes the initial (parentless) commit list its files too.
        proc = self._run_git(
            ["diff-tree", "--no-commit-id", "--name-only", "-r", "--root", commit_sha]
        )
        return [line for line in proc.stdout.splitlines() if line.strip()]

    def diff(
        self, workflow_path: Path, *, against: DiffRef = "HEAD"
    ) -> DiffResult:
        """Diff the working-tree workflow against a git ref, node by node."""
        if not self._is_repo():
            raise WorkflowVCSError(
                f"{self.workflows_dir} is not a git repo; run init() first"
            )
        rel = self._rel(workflow_path)
        target = self.workflows_dir / rel

        old_content, old_hash = self._blob_at_ref(against, rel)
        new_content, new_hash = self._working_tree_blob(target)

        raw_diff = self._run_git(
            ["diff", against, "--", rel], check=False
        ).stdout

        old_nodes = self._index_nodes(old_content)
        new_nodes = self._index_nodes(new_content)

        added = [
            {"id": nid, "class_type": new_nodes[nid]["class_type"]}
            for nid in new_nodes
            if nid not in old_nodes
        ]
        removed = [
            {"id": nid, "class_type": old_nodes[nid]["class_type"]}
            for nid in old_nodes
            if nid not in new_nodes
        ]
        modified = [
            {"id": nid, "class_type": new_nodes[nid]["class_type"]}
            for nid in new_nodes
            if nid in old_nodes and new_nodes[nid]["repr"] != old_nodes[nid]["repr"]
        ]

        return DiffResult(
            old_hash=old_hash,
            new_hash=new_hash,
            nodes_added=added,
            nodes_removed=removed,
            nodes_modified=modified,
            raw_diff=raw_diff,
        )

    def _blob_at_ref(self, ref: str, rel: str) -> tuple[Optional[str], Optional[str]]:
        """Return (json-text, blob-sha) for ``rel`` at ``ref`` or (None, None)."""
        show = self._run_git(["show", f"{ref}:{rel}"], check=False)
        if show.returncode != 0:
            return None, None
        blob = self._run_git(["rev-parse", f"{ref}:{rel}"], check=False)
        blob_sha = blob.stdout.strip() if blob.returncode == 0 else None
        return show.stdout, blob_sha

    def _working_tree_blob(self, target: Path) -> tuple[Optional[str], Optional[str]]:
        if not target.exists():
            return None, None
        text = target.read_text(encoding="utf-8")
        # Absolute path so it resolves regardless of the git subprocess cwd.
        blob = self._run_git(
            ["hash-object", str(Path(target).resolve())], check=False
        )
        return text, (blob.stdout.strip() if blob.returncode == 0 else None)

    @staticmethod
    def _index_nodes(content: Optional[str]) -> dict[str, dict[str, str]]:
        """Map node id -> {class_type, repr} for both ComfyUI JSON formats.

        Supports the UI graph export (``{"nodes": [{"id", "type", ...}]}``) and
        the API/prompt format (``{"3": {"class_type": ...}}``).
        """
        if not content:
            return {}
        try:
            data = json.loads(content)
        except (json.JSONDecodeError, ValueError):
            return {}

        nodes: dict[str, dict[str, str]] = {}

        if isinstance(data, dict) and isinstance(data.get("nodes"), list):
            for node in data["nodes"]:
                if not isinstance(node, dict):
                    continue
                nid = str(node.get("id"))
                class_type = str(node.get("type", "unknown"))
                nodes[nid] = {
                    "class_type": class_type,
                    "repr": json.dumps(node, sort_keys=True),
                }
            return nodes

        if isinstance(data, dict):
            for nid, node in data.items():
                if isinstance(node, dict) and "class_type" in node:
                    nodes[str(nid)] = {
                        "class_type": str(node.get("class_type", "unknown")),
                        "repr": json.dumps(node, sort_keys=True),
                    }
        return nodes

    def history(
        self, workflow_path: Path, *, limit: int = 10
    ) -> list[CommitInfo]:
        """Return recent commits touching ``workflow_path`` (newest first)."""
        if not self._is_repo() or not self._has_commits():
            return []
        rel = self._rel(workflow_path)
        # Commits are matched by their ``<workflow_name>: ...`` subject prefix
        # rather than a path filter, so metadata-only re-test commits (recorded
        # with --allow-empty, no file change) are still included in history.
        prefix = f"{Path(rel).name}:"
        sep = "\x1f"
        rec = "\x1e"
        proc = self._run_git(
            ["log", f"--format=%H{sep}%aI{sep}%s{sep}%b{rec}"],
            check=False,
        )
        commits: list[CommitInfo] = []
        for record in proc.stdout.split(rec):
            record = record.strip("\n")
            if not record.strip():
                continue
            parts = record.split(sep)
            if len(parts) < 4:
                continue
            sha, date, subject, body = parts[0], parts[1], parts[2], parts[3]
            if not subject.startswith(prefix):
                continue
            commits.append(
                CommitInfo(
                    sha=sha,
                    date=date,
                    subject=subject,
                    metadata=self._parse_metadata(body),
                )
            )
            if len(commits) >= limit:
                break
        return commits

    @staticmethod
    def _parse_metadata(body: str) -> dict[str, str]:
        mapping = {
            "test result": "test_result",
            "seed": "seed",
            "model hash": "model_hash",
            "tester": "tester",
        }
        meta: dict[str, str] = {}
        for line in body.splitlines():
            if ":" not in line:
                continue
            key, _, value = line.partition(":")
            norm = mapping.get(key.strip().lower())
            if norm:
                meta[norm] = value.strip()
        return meta

    def sync_meta_template(self, workflow_path: Path, metadata: dict) -> None:
        """Append ``metadata`` to a sidecar test-history file for the workflow."""
        meta_dir = self.workflows_dir / META_SUBDIR
        meta_dir.mkdir(parents=True, exist_ok=True)
        name = Path(workflow_path).stem
        meta_file = meta_dir / f"{name}.meta.json"

        if meta_file.exists():
            try:
                existing = json.loads(meta_file.read_text(encoding="utf-8"))
            except (json.JSONDecodeError, ValueError):
                existing = {}
        else:
            existing = {}

        history = existing.get("history")
        if not isinstance(history, list):
            history = []
        history.append(metadata)

        payload = {
            "workflow": Path(workflow_path).name,
            "updated": datetime.now(timezone.utc).isoformat(),
            "history": history,
        }
        meta_file.write_text(
            json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
        )

    def read_meta(self, workflow_path: Path) -> dict[str, Any]:
        """Return the sidecar metadata for a workflow (empty dict if none)."""
        name = Path(workflow_path).stem
        meta_file = self.workflows_dir / META_SUBDIR / f"{name}.meta.json"
        if not meta_file.exists():
            return {}
        try:
            return json.loads(meta_file.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, ValueError):
            return {}


# -- CLI ------------------------------------------------------------------


def _print_json(payload: Any) -> None:
    print(json.dumps(payload, indent=2, ensure_ascii=False))


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="workflow_vcs.py",
        description="Version control for ComfyUI workflow JSONs.",
    )
    parser.add_argument(
        "--workflows-dir",
        type=Path,
        default=WORKFLOWS_DIR,
        help=f"workflow directory (default: {WORKFLOWS_DIR})",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("init", help="initialise the git repo and .gitignore")

    p_commit = sub.add_parser("commit", help="commit a workflow with metadata")
    p_commit.add_argument("workflow", help="workflow filename (relative to dir)")
    p_commit.add_argument("--message", "-m", required=True, help="change description")
    p_commit.add_argument("--seed", type=int, default=None, help="KSampler seed")
    p_commit.add_argument("--model-hash", default=None, help="checkpoint sha256")
    p_commit.add_argument(
        "--test-result",
        choices=VALID_TEST_RESULTS,
        default="untested",
    )
    p_commit.add_argument("--tester", default="cursor-agent")

    p_diff = sub.add_parser("diff", help="node-level diff against a ref")
    p_diff.add_argument("workflow")
    p_diff.add_argument(
        "--against", default="HEAD", help="git ref (HEAD, HEAD~1, HEAD~3, main, ...)"
    )

    p_hist = sub.add_parser("history", help="show commit history for a workflow")
    p_hist.add_argument("workflow")
    p_hist.add_argument("--limit", type=int, default=10)

    p_meta = sub.add_parser("meta", help="show sidecar metadata for a workflow")
    p_meta.add_argument("workflow")

    return parser


def main(argv: Optional[list[str]] = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)
    vcs = WorkflowVCS(args.workflows_dir)

    try:
        if args.command == "init":
            vcs.init()
            print(f"Initialised workflow repo at {vcs.workflows_dir}")
            return 0

        if args.command == "commit":
            result = vcs.commit(
                Path(args.workflow),
                message=args.message,
                seed=args.seed,
                model_hash=args.model_hash,
                test_result=args.test_result,
                tester=args.tester,
            )
            print(f"Committed {result.commit_sha[:10]} ({args.test_result})")
            _print_json(asdict(result))
            return 0

        if args.command == "diff":
            result = vcs.diff(Path(args.workflow), against=args.against)
            print(
                f"Nodes added: {len(result.nodes_added)} | "
                f"removed: {len(result.nodes_removed)} | "
                f"modified: {len(result.nodes_modified)}"
            )
            _print_json(asdict(result))
            return 0

        if args.command == "history":
            commits = vcs.history(Path(args.workflow), limit=args.limit)
            _print_json([asdict(c) for c in commits])
            return 0

        if args.command == "meta":
            _print_json(vcs.read_meta(Path(args.workflow)))
            return 0
    except WorkflowVCSError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1

    parser.error(f"unknown command: {args.command}")
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
