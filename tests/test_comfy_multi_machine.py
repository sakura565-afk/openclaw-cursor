"""Tests for ComfyUI multi-machine pool + batch renderer."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict
import pytest

from scripts.comfy_machine_pool import (
    Machine,
    MachinePool,
    MachineStatus,
    default_machines,
    machines_from_env,
    probe_machine,
    resolve_machines,
)
from scripts import comfy_multi_render as multi
from scripts.comfy_multi_render import (
    RenderJob,
    collect_jobs,
    dispatch_jobs,
    load_workflow,
)


API_WORKFLOW = {
    "3": {
        "class_type": "KSampler",
        "inputs": {"seed": 1, "steps": 20, "cfg": 7.0},
    },
    "4": {
        "class_type": "SaveImage",
        "inputs": {"filename_prefix": "test", "images": ["3", 0]},
    },
}


def test_default_machines_include_local_and_work_pc() -> None:
    machines = default_machines()
    names = {m.name for m in machines}
    assert names == {"Local", "Work-PC"}
    assert any("127.0.0.1" in m.url for m in machines)
    assert any("192.168.31.180" in m.url for m in machines)


def test_machines_from_env_json() -> None:
    env = {
        "COMFY_MACHINES": json.dumps(
            [
                {"name": "A", "url": "http://a:8188", "priority": 1},
                {"name": "B", "url": "http://b:8188"},
            ]
        )
    }
    machines = machines_from_env(env)
    assert machines is not None
    assert [m.name for m in machines] == ["A", "B"]
    assert machines[0].priority == 1


def test_machines_from_env_csv_and_single_url() -> None:
    csv_machines = machines_from_env({"COMFY_MACHINES": "http://x:1, http://y:2"})
    assert csv_machines is not None
    assert len(csv_machines) == 2
    single = machines_from_env({"COMFY_URL": "http://only:8188"})
    assert single is not None
    assert single[0].name == "custom"


def test_resolve_machines_prefers_explicit() -> None:
    custom = [Machine(name="X", url="http://x")]
    assert resolve_machines(custom) == custom


def test_load_score_offline_sorts_last() -> None:
    m = Machine(name="L", url="http://127.0.0.1:8188")
    offline = MachineStatus(machine=m, online=False, error="down")
    online = MachineStatus(machine=m, online=True, queue_pending=2, queue_running=1)
    assert online.load_score < offline.load_score


def test_probe_machine_online(monkeypatch: pytest.MonkeyPatch) -> None:
    machine = Machine(name="Local", url="http://127.0.0.1:8188")

    def fake_http(url: str, timeout: float) -> Dict[str, Any]:
        if url.endswith("/system_stats"):
            return {"devices": [{"vram_free": 4 * 1024**3, "vram_total": 12 * 1024**3}]}
        if url.endswith("/queue"):
            return {"queue_pending": [1, 2], "queue_running": [1]}
        raise AssertionError(url)

    monkeypatch.setattr("scripts.comfy_machine_pool._http_json", fake_http)
    status = probe_machine(machine)
    assert status.online
    assert status.queue_pending == 2
    assert status.queue_running == 1
    assert status.vram_free_mb == pytest.approx(4096.0)


def test_probe_machine_offline(monkeypatch: pytest.MonkeyPatch) -> None:
    import urllib.error

    def boom(url: str, timeout: float) -> Any:
        raise urllib.error.URLError("refused")

    monkeypatch.setattr("scripts.comfy_machine_pool._http_json", boom)
    status = probe_machine(Machine(name="Local", url="http://127.0.0.1:8188"))
    assert not status.online
    assert status.error


def test_pool_pick_least_busy(monkeypatch: pytest.MonkeyPatch) -> None:
    machines = [
        Machine(name="Busy", url="http://busy:8188", priority=10),
        Machine(name="Idle", url="http://idle:8188", priority=20),
    ]

    def fake_probe(machine: Machine, timeout: float = 5.0) -> MachineStatus:
        if machine.name == "Busy":
            return MachineStatus(machine=machine, online=True, queue_pending=5, queue_running=1)
        return MachineStatus(machine=machine, online=True, queue_pending=0, queue_running=0)

    monkeypatch.setattr("scripts.comfy_machine_pool.probe_machine", fake_probe)
    pool = MachinePool(machines=machines)
    chosen = pool.pick()
    assert chosen is not None
    assert chosen.machine.name == "Idle"


def test_load_workflow_api_and_wrapped(tmp_path: Path) -> None:
    direct = tmp_path / "direct.json"
    direct.write_text(json.dumps(API_WORKFLOW), encoding="utf-8")
    assert load_workflow(direct)["3"]["class_type"] == "KSampler"

    wrapped = tmp_path / "wrapped.json"
    wrapped.write_text(json.dumps({"prompt": API_WORKFLOW}), encoding="utf-8")
    assert "4" in load_workflow(wrapped)


def test_load_workflow_rejects_ui_format(tmp_path: Path) -> None:
    ui = tmp_path / "ui.json"
    ui.write_text(json.dumps({"nodes": [{"id": 1}], "links": []}), encoding="utf-8")
    with pytest.raises(multi.RenderError, match="UI-format"):
        load_workflow(ui)


def test_collect_jobs_seed_variants(tmp_path: Path) -> None:
    wf = tmp_path / "job.json"
    wf.write_text(json.dumps(API_WORKFLOW), encoding="utf-8")
    jobs = collect_jobs(workflow=wf, workflow_dir=None, count=3, seed=100)
    assert len(jobs) == 3
    assert [j.seed_override for j in jobs] == [100, 101, 102]
    assert jobs[1].prompt_graph()["3"]["inputs"]["seed"] == 101


def test_dispatch_dry_run_without_live_hosts(tmp_path: Path) -> None:
    """Dry-run must not require reachable ComfyUI endpoints."""
    jobs = [RenderJob(job_id="a", workflow=API_WORKFLOW, label="a")]
    pool = MachinePool(machines=[Machine(name="Local", url="http://127.0.0.1:59999")])
    results = dispatch_jobs(
        jobs,
        pool=pool,
        output_dir=tmp_path / "out",
        dry_run=True,
        logger=multi.MarkdownLogger(tmp_path / "log.md"),
    )
    assert len(results) == 1
    assert results[0].success
    assert results[0].machine == "Local"


def test_dispatch_dry_run(tmp_path: Path) -> None:
    machines = [Machine(name="Local", url="http://127.0.0.1:8188")]
    jobs = [
        RenderJob(job_id="a", workflow=API_WORKFLOW, label="a"),
        RenderJob(job_id="b", workflow=API_WORKFLOW, label="b"),
    ]
    pool = MachinePool(machines=machines)
    results = dispatch_jobs(
        jobs,
        pool=pool,
        output_dir=tmp_path / "out",
        parallel=2,
        dry_run=True,
        logger=multi.MarkdownLogger(tmp_path / "log.md"),
    )
    assert len(results) == 2
    assert all(r.success and r.prompt_id == "dry-run" for r in results)


def test_cli_pool_list() -> None:
    from scripts.comfy_machine_pool import main

    assert main(["list"]) == 0


def test_cli_multi_render_dry_run(tmp_path: Path) -> None:
    wf = tmp_path / "w.json"
    wf.write_text(json.dumps(API_WORKFLOW), encoding="utf-8")

    rc = multi.main(
        [
            "render",
            "--workflow",
            str(wf),
            "--output-dir",
            str(tmp_path / "out"),
            "--dry-run",
            "--log",
            str(tmp_path / "log.md"),
            "--json-out",
            str(tmp_path / "results.json"),
            "--count",
            "2",
            "--seed",
            "42",
        ]
    )
    assert rc == 0
    payload = json.loads((tmp_path / "results.json").read_text(encoding="utf-8"))
    assert len(payload) == 2
    assert all(row["success"] for row in payload)


def test_machine_pool_cli_pick_json(monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    from scripts import comfy_machine_pool as pool_mod

    def fake_probe(machine: Machine, timeout: float = 5.0) -> MachineStatus:
        return MachineStatus(machine=machine, online=True, queue_pending=0)

    monkeypatch.setattr(pool_mod, "probe_machine", fake_probe)
    assert pool_mod.main(["pick"]) == 0
    data = json.loads(capsys.readouterr().out)
    assert data["online"] is True
    assert "machine" in data
