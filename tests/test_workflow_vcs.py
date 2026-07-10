"""Unit tests for scripts/workflow_vcs.py.

Runs against real git in a tempdir (fast: three tiny JSON commits complete in
well under a second). Uses the repo's real ``flux_klein_face_swap_gguf.json``
fixture when present, else a minimal mock workflow.
"""

from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest

from scripts import workflow_vcs
from scripts.workflow_vcs import WorkflowVCS, WorkflowVCSError

REPO_ROOT = Path(__file__).resolve().parents[1]
REAL_FIXTURE = REPO_ROOT / "comfyui_workflows" / "flux_klein_face_swap_gguf.json"


def _mock_workflow() -> dict:
    """Minimal ComfyUI UI-format workflow used when no real fixture exists."""
    return {
        "last_node_id": 3,
        "last_link_id": 2,
        "nodes": [
            {
                "id": 1,
                "type": "UnetLoaderGGUF",
                "widgets_values": ["flux-2-klein-9b-Q4_0.gguf"],
            },
            {
                "id": 2,
                "type": "KSampler",
                "widgets_values": [12345, "fixed", 20, 1.5, "euler", "normal", 1.0],
            },
            {"id": 3, "type": "VAEDecode", "widgets_values": []},
        ],
        "links": [],
        "version": 0.4,
    }


@pytest.fixture()
def workflow_file(tmp_path: Path) -> Path:
    """A workflow JSON inside a fresh workflows dir (real fixture if available)."""
    wf_dir = tmp_path / "comfyui_workflows"
    wf_dir.mkdir()
    dest = wf_dir / "flux_klein_face_swap_gguf.json"
    if REAL_FIXTURE.exists():
        shutil.copy(REAL_FIXTURE, dest)
    else:
        dest.write_text(json.dumps(_mock_workflow(), indent=2), encoding="utf-8")
    return dest


@pytest.fixture()
def vcs(workflow_file: Path) -> WorkflowVCS:
    instance = WorkflowVCS(workflow_file.parent)
    instance.init()
    return instance


def test_init_creates_git_and_gitignore(tmp_path: Path) -> None:
    wf_dir = tmp_path / "comfyui_workflows"
    instance = WorkflowVCS(wf_dir)
    instance.init()

    assert (wf_dir / ".git").exists()
    gitignore = wf_dir / ".gitignore"
    assert gitignore.exists()
    body = gitignore.read_text(encoding="utf-8")
    assert "*.bak" in body
    assert "*.tmp" in body
    assert "__pycache__/" in body


def test_init_is_idempotent(tmp_path: Path) -> None:
    instance = WorkflowVCS(tmp_path / "comfyui_workflows")
    instance.init()
    instance.init()  # second call must not raise
    assert (tmp_path / "comfyui_workflows" / ".git").exists()


def test_commit_produces_expected_metadata(vcs: WorkflowVCS, workflow_file: Path) -> None:
    result = vcs.commit(
        Path(workflow_file.name),
        message="fix sampler cfg",
        seed=12345,
        model_hash="abc123",
        test_result="approved",
        tester="cursor-agent",
    )

    assert len(result.commit_sha) == 40
    assert result.metadata["test_result"] == "approved"
    assert result.metadata["seed"] == 12345
    assert result.metadata["model_hash"] == "abc123"
    assert workflow_file.name in result.files_changed

    body = vcs._run_git(["log", "-1", "--format=%B"]).stdout
    assert body.startswith(f"{workflow_file.name}: fix sampler cfg")
    assert "Test result: approved" in body
    assert "Seed: 12345" in body
    assert "Model hash: abc123" in body
    assert "Tester: cursor-agent" in body


def test_commit_writes_meta_sidecar(vcs: WorkflowVCS, workflow_file: Path) -> None:
    vcs.commit(Path(workflow_file.name), message="first", test_result="untested")
    vcs.commit(Path(workflow_file.name), message="second", test_result="approved")

    meta = vcs.read_meta(Path(workflow_file.name))
    assert meta["workflow"] == workflow_file.name
    assert len(meta["history"]) == 2
    assert meta["history"][-1]["test_result"] == "approved"


def test_commit_rejects_invalid_test_result(vcs: WorkflowVCS, workflow_file: Path) -> None:
    with pytest.raises(WorkflowVCSError):
        vcs.commit(
            Path(workflow_file.name),
            message="bad",
            test_result="maybe",  # type: ignore[arg-type]
        )


def test_diff_zero_changes_on_head(vcs: WorkflowVCS, workflow_file: Path) -> None:
    vcs.commit(Path(workflow_file.name), message="baseline", test_result="untested")
    result = vcs.diff(Path(workflow_file.name), against="HEAD")

    assert result.nodes_added == []
    assert result.nodes_removed == []
    assert result.nodes_modified == []


def test_diff_detects_added_removed_modified(vcs: WorkflowVCS, workflow_file: Path) -> None:
    vcs.commit(Path(workflow_file.name), message="baseline", test_result="untested")

    data = json.loads(workflow_file.read_text(encoding="utf-8"))
    nodes = data["nodes"]
    # Remove one node (the VAEDecode / a decode node), add one, modify KSampler.
    original_ids = {str(n["id"]) for n in nodes}
    nodes = [n for n in nodes if n.get("type") != "VAEDecode"]
    new_id = max(int(i) for i in original_ids) + 1
    nodes.append({"id": new_id, "type": "SaveImage", "widgets_values": ["out"]})
    for node in nodes:
        if node.get("type") == "KSampler":
            node["widgets_values"] = [999, "fixed", 25, 1.0, "euler", "normal", 1.0]
    data["nodes"] = nodes
    workflow_file.write_text(json.dumps(data, indent=2), encoding="utf-8")

    result = vcs.diff(Path(workflow_file.name), against="HEAD")

    added_types = {c["class_type"] for c in result.nodes_added}
    removed_types = {c["class_type"] for c in result.nodes_removed}
    modified_types = {c["class_type"] for c in result.nodes_modified}

    assert "SaveImage" in added_types
    assert "VAEDecode" in removed_types
    assert "KSampler" in modified_types
    assert result.raw_diff != ""
    assert result.old_hash is not None
    assert result.new_hash is not None
    assert result.old_hash != result.new_hash


def test_history_reverse_chronological(vcs: WorkflowVCS, workflow_file: Path) -> None:
    vcs.commit(Path(workflow_file.name), message="one", test_result="untested")
    vcs.commit(Path(workflow_file.name), message="two", test_result="rejected")
    vcs.commit(Path(workflow_file.name), message="three", test_result="approved")

    commits = vcs.history(Path(workflow_file.name), limit=5)

    assert len(commits) == 3
    assert commits[0].subject.endswith("three")
    assert commits[1].subject.endswith("two")
    assert commits[2].subject.endswith("one")
    assert commits[0].metadata["test_result"] == "approved"
    assert commits[1].metadata["test_result"] == "rejected"


def test_history_respects_limit(vcs: WorkflowVCS, workflow_file: Path) -> None:
    for i in range(4):
        vcs.commit(Path(workflow_file.name), message=f"c{i}", test_result="untested")
    commits = vcs.history(Path(workflow_file.name), limit=2)
    assert len(commits) == 2


def test_commit_without_init_raises(tmp_path: Path) -> None:
    wf_dir = tmp_path / "comfyui_workflows"
    wf_dir.mkdir()
    wf = wf_dir / "wf.json"
    wf.write_text(json.dumps(_mock_workflow()), encoding="utf-8")
    instance = WorkflowVCS(wf_dir)
    with pytest.raises(WorkflowVCSError):
        instance.commit(Path("wf.json"), message="x")


def test_cli_init_and_commit(tmp_path: Path, workflow_file: Path) -> None:
    wf_dir = workflow_file.parent
    assert workflow_vcs.main(["--workflows-dir", str(wf_dir), "init"]) == 0
    code = workflow_vcs.main(
        [
            "--workflows-dir",
            str(wf_dir),
            "commit",
            workflow_file.name,
            "--message",
            "cli commit",
            "--seed",
            "42",
            "--test-result",
            "approved",
        ]
    )
    assert code == 0
    instance = WorkflowVCS(wf_dir)
    commits = instance.history(Path(workflow_file.name), limit=1)
    assert commits[0].metadata["seed"] == "42"
