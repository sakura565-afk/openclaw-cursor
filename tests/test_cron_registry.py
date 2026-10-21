"""Tests for scripts/cron_registry.py (no network, no real data/ access)."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Sequence

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts import cron_registry as cr  # noqa: E402

NOW = datetime(2026, 10, 21, 12, 0, 0, tzinfo=timezone.utc)


# --------------------------------------------------------------------------- #
# Fixtures / helpers
# --------------------------------------------------------------------------- #


def write_registry(root: Path, tasks: list[dict[str, Any]], **extra: Any) -> Path:
    payload: dict[str, Any] = {"version": 1, "tasks": tasks}
    payload.update(extra)
    path = root / "data" / "cron_registry.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return path


def write_state(root: Path, relative: str, payload: dict[str, Any], *, age_hours: float) -> Path:
    path = root / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    stamp = (NOW - timedelta(hours=age_hours)).timestamp()
    os.utime(path, (stamp, stamp))
    return path


def build(root: Path, tasks: list[dict[str, Any]], **extra: Any) -> cr.CronRegistry:
    path = write_registry(root, tasks, **extra)
    return cr.CronRegistry(registry_path=path, workspace_root=root)


def health(name: str, **kwargs: Any) -> cr.TaskHealth:
    return cr.TaskHealth(name=name, **kwargs)


def fake_schtasks(csv_text: str, *, returncode: int = 0):
    def runner(cmd: Sequence[str], **kwargs: Any) -> subprocess.CompletedProcess[str]:
        assert list(cmd) == ["schtasks", "/Query", "/FO", "CSV", "/V"]
        return subprocess.CompletedProcess(list(cmd), returncode, csv_text, "")

    return runner


SCHTASKS_CSV = """"HostName","TaskName","Next Run Time","Status","Last Run Time","Last Result"
"PC","\\HF-Trending-Daily","21.10.2026 8:00:00","Ready","20.10.2026 8:00:00","0"
"PC","\\Ollama AI server","N/A","Running","20.10.2026 7:00:00","267009"
"PC","\\Some-Other-Task","21.10.2026 9:00:00","Ready","20.10.2026 9:00:00","0"
"PC","\\Microsoft\\Windows\\UpdateOrchestrator\\Reboot","N/A","Ready","N/A","1"
"""


# --------------------------------------------------------------------------- #
# load() / schema validation
# --------------------------------------------------------------------------- #


def test_load_parses_tasks_and_defaults(tmp_path: Path) -> None:
    registry = build(
        tmp_path,
        [{"name": "A", "period_hours": 0.25, "state_file": "data/a.json"}],
    ).load()
    assert registry.version == 1
    assert registry.stale_factor == pytest.approx(1.5)
    assert registry.tasks[0].enabled is True
    assert registry.tasks[0].stale_after_hours() == pytest.approx(0.375)


def test_load_broken_json_raises_friendly_error(tmp_path: Path) -> None:
    path = tmp_path / "data" / "cron_registry.json"
    path.parent.mkdir(parents=True)
    path.write_text('{"version": 1, "tasks": [', encoding="utf-8")
    registry = cr.CronRegistry(registry_path=path, workspace_root=tmp_path)

    with pytest.raises(cr.RegistryError) as excinfo:
        registry.load()

    message = str(excinfo.value)
    assert "битый JSON" in message
    assert "строка" in message
    assert "Traceback" not in message


def test_load_reports_all_schema_problems(tmp_path: Path) -> None:
    registry = build(
        tmp_path,
        [
            {"name": "", "period_hours": 1},
            {"name": "B", "period_hours": "daily"},
            {"name": "C", "enabled": "yes"},
        ],
    )
    with pytest.raises(cr.RegistryError) as excinfo:
        registry.load()

    message = str(excinfo.value)
    assert "tasks[0].name" in message
    assert "tasks[1].period_hours" in message
    assert "tasks[2].enabled" in message


def test_load_missing_file_raises(tmp_path: Path) -> None:
    registry = cr.CronRegistry(registry_path=tmp_path / "nope.json", workspace_root=tmp_path)
    with pytest.raises(cr.RegistryError):
        registry.load()


# --------------------------------------------------------------------------- #
# check(): state parsing
# --------------------------------------------------------------------------- #


def test_state_last_status_ok_is_ok(tmp_path: Path) -> None:
    write_state(tmp_path, "data/a.json", {"last_status": "ok"}, age_hours=1)
    registry = build(tmp_path, [{"name": "A", "period_hours": 24, "state_file": "data/a.json"}])

    (item,) = registry.check(NOW)

    assert item.status == cr.STATUS_OK
    assert item.last_status == "ok"


def test_state_last_status_error_is_failing(tmp_path: Path) -> None:
    write_state(
        tmp_path,
        "data/a.json",
        {"last_status": "error", "last_success": "2026-10-19T06:00:00+00:00"},
        age_hours=1,
    )
    registry = build(tmp_path, [{"name": "A", "period_hours": 24, "state_file": "data/a.json"}])

    (item,) = registry.check(NOW)

    assert item.status == cr.STATUS_FAILING
    assert item.last_success == "2026-10-19T06:00:00+00:00"
    assert "last_status=error" in item.detail


def test_state_ok_boolean_false_is_failing(tmp_path: Path) -> None:
    write_state(tmp_path, "data/a.json", {"ok": False}, age_hours=1)
    registry = build(tmp_path, [{"name": "A", "period_hours": 24, "state_file": "data/a.json"}])

    assert registry.check(NOW)[0].status == cr.STATUS_FAILING


def test_state_without_status_field_is_ok(tmp_path: Path) -> None:
    write_state(tmp_path, "data/a.json", {"seen": {"x": 1}}, age_hours=2)
    registry = build(tmp_path, [{"name": "A", "period_hours": 24, "state_file": "data/a.json"}])

    (item,) = registry.check(NOW)

    assert item.status == cr.STATUS_OK
    assert item.last_status is None


def test_glob_state_resolves_newest_by_mtime(tmp_path: Path) -> None:
    write_state(tmp_path, "data/h_2026-10-19.json", {"ok": True}, age_hours=48)
    newest = write_state(tmp_path, "data/h_2026-10-21.json", {"ok": True}, age_hours=3)
    registry = build(
        tmp_path, [{"name": "A", "period_hours": 24, "state_file": "data/h_*.json"}]
    )

    (item,) = registry.check(NOW)

    assert item.state_path == str(newest)
    assert item.state_age_hours == pytest.approx(3.0, abs=0.01)
    assert item.status == cr.STATUS_OK


def test_missing_state_file(tmp_path: Path) -> None:
    registry = build(
        tmp_path, [{"name": "A", "period_hours": 24, "state_file": "data/absent.json"}]
    )

    (item,) = registry.check(NOW)

    assert item.status == cr.STATUS_MISSING_STATE
    assert "data/absent.json" in item.detail


def test_disabled_task_is_disabled(tmp_path: Path) -> None:
    registry = build(
        tmp_path,
        [{"name": "A", "enabled": False, "period_hours": 24, "state_file": "data/absent.json"}],
    )

    assert registry.check(NOW)[0].status == cr.STATUS_DISABLED


def test_task_absent_from_system_is_unknown_task(tmp_path: Path) -> None:
    write_state(tmp_path, "data/a.json", {"ok": True}, age_hours=1)
    registry = build(tmp_path, [{"name": "A", "period_hours": 24, "state_file": "data/a.json"}])

    (item,) = registry.check(NOW, system_tasks=["\\Other"])

    assert item.status == cr.STATUS_UNKNOWN_TASK


def test_task_without_observables_is_unknown_task(tmp_path: Path) -> None:
    registry = build(tmp_path, [{"name": "A", "period_hours": 24}])

    (item,) = registry.check(NOW)

    assert item.status == cr.STATUS_UNKNOWN_TASK
    assert "state_file" in item.detail


# --------------------------------------------------------------------------- #
# check(): the "missed N runs" window
# --------------------------------------------------------------------------- #


def test_state_older_than_one_and_half_periods_is_stale(tmp_path: Path) -> None:
    write_state(tmp_path, "data/a.json", {"last_status": "ok"}, age_hours=37)
    registry = build(tmp_path, [{"name": "A", "period_hours": 24, "state_file": "data/a.json"}])

    (item,) = registry.check(NOW)

    assert item.status == cr.STATUS_STALE
    assert item.stale_after_hours == pytest.approx(36.0)


def test_exactly_on_the_boundary_is_still_ok(tmp_path: Path) -> None:
    write_state(tmp_path, "data/a.json", {"last_status": "ok"}, age_hours=36)
    registry = build(tmp_path, [{"name": "A", "period_hours": 24, "state_file": "data/a.json"}])

    assert registry.check(NOW)[0].status == cr.STATUS_OK


def test_failing_wins_over_stale(tmp_path: Path) -> None:
    write_state(tmp_path, "data/a.json", {"last_status": "failed"}, age_hours=90)
    registry = build(tmp_path, [{"name": "A", "period_hours": 24, "state_file": "data/a.json"}])

    (item,) = registry.check(NOW)

    assert item.status == cr.STATUS_FAILING
    assert "не обновлялся" in item.detail


def test_per_task_stale_factor_override(tmp_path: Path) -> None:
    write_state(tmp_path, "data/a.json", {"ok": True}, age_hours=30)
    registry = build(
        tmp_path,
        [
            {
                "name": "A",
                "period_hours": 24,
                "stale_factor": 1.0,
                "state_file": "data/a.json",
            }
        ],
    )

    assert registry.check(NOW)[0].status == cr.STATUS_STALE


def test_fifteen_minute_task_stale_after_22_minutes(tmp_path: Path) -> None:
    write_state(tmp_path, "data/poll.json", {"ok": True}, age_hours=0.5)
    registry = build(
        tmp_path, [{"name": "Poll", "period_hours": 0.25, "state_file": "data/poll.json"}]
    )

    (item,) = registry.check(NOW)

    assert item.status == cr.STATUS_STALE
    assert item.stale_after_hours == pytest.approx(0.375)


# --------------------------------------------------------------------------- #
# check(): log signals
# --------------------------------------------------------------------------- #


def test_nonzero_exit_in_log_tail_is_failing(tmp_path: Path) -> None:
    write_state(tmp_path, "data/a.json", {"ok": True}, age_hours=1)
    log = tmp_path / "logs" / "a_2026-10-21.out.log"
    log.parent.mkdir(parents=True)
    log.write_text("==== 2026-10-21T11:00:00Z START ====\nboom\n==== END exit=1 ====\n", "utf-8")
    registry = build(
        tmp_path,
        [
            {
                "name": "A",
                "period_hours": 24,
                "state_file": "data/a.json",
                "log_file": "logs/a_*.out.log",
            }
        ],
    )

    (item,) = registry.check(NOW)

    assert item.status == cr.STATUS_FAILING
    assert item.log_exit_code == 1
    assert item.log_last_line == "==== END exit=1 ===="


def test_log_only_task_uses_log_age_for_liveness(tmp_path: Path) -> None:
    log = tmp_path / "logs" / "a.out.log"
    log.parent.mkdir(parents=True)
    log.write_text("==== END exit=0 ====\n", encoding="utf-8")
    stamp = (NOW - timedelta(hours=72)).timestamp()
    os.utime(log, (stamp, stamp))
    registry = build(
        tmp_path, [{"name": "A", "period_hours": 24, "log_file": "logs/a.out.log"}]
    )

    (item,) = registry.check(NOW)

    assert item.status == cr.STATUS_STALE
    assert item.log_age_hours == pytest.approx(72.0, abs=0.01)


# --------------------------------------------------------------------------- #
# diff()
# --------------------------------------------------------------------------- #


def test_diff_no_changes_returns_empty_list() -> None:
    registry = cr.CronRegistry(registry_path=Path("unused.json"), workspace_root=Path("."))
    previous = [
        health("A", status=cr.STATUS_OK, period_hours=24, state_age_hours=1.0),
        health("B", status=cr.STATUS_STALE, period_hours=24, state_age_hours=100.0),
    ]
    current = [
        health("A", status=cr.STATUS_OK, period_hours=24, state_age_hours=1.5),
        health("B", status=cr.STATUS_STALE, period_hours=24, state_age_hours=101.0),
    ]

    assert registry.diff(previous, current) == []


def test_diff_status_change_is_single_entry() -> None:
    registry = cr.CronRegistry(registry_path=Path("unused.json"), workspace_root=Path("."))
    previous = [health("A", status=cr.STATUS_OK, period_hours=24, state_age_hours=1.0)]
    current = [
        health(
            "A",
            status=cr.STATUS_STALE,
            period_hours=24,
            stale_after_hours=36.0,
            state_age_hours=40.0,
            detail="state не обновлялся 40.0ч",
        )
    ]

    (change,) = registry.diff(previous, current)

    assert change.kind == cr.CHANGE_STATUS
    assert change.level == cr.LEVEL_ALERT
    assert (change.previous_status, change.current_status) == (cr.STATUS_OK, cr.STATUS_STALE)


def test_diff_new_task_is_single_entry() -> None:
    registry = cr.CronRegistry(registry_path=Path("unused.json"), workspace_root=Path("."))
    current = [
        health("A", status=cr.STATUS_OK, period_hours=24, state_age_hours=1.0),
        health("B", status=cr.STATUS_OK, period_hours=24, state_age_hours=1.0),
    ]
    previous = [current[0]]

    (change,) = registry.diff(previous, current)

    assert (change.name, change.kind) == ("B", cr.CHANGE_APPEARED)
    assert change.level == cr.LEVEL_INFO


def test_diff_disappeared_task_is_single_entry() -> None:
    registry = cr.CronRegistry(registry_path=Path("unused.json"), workspace_root=Path("."))
    previous = [
        health("A", status=cr.STATUS_OK, period_hours=24, state_age_hours=1.0),
        health("B", status=cr.STATUS_OK, period_hours=24, state_age_hours=1.0),
    ]
    current = [previous[0]]

    (change,) = registry.diff(previous, current)

    assert (change.name, change.kind) == ("B", cr.CHANGE_DISAPPEARED)
    assert change.current_status is None


def test_diff_age_bucket_change_is_info_entry() -> None:
    registry = cr.CronRegistry(registry_path=Path("unused.json"), workspace_root=Path("."))
    previous = [
        health(
            "A", status=cr.STATUS_OK, period_hours=24, stale_after_hours=36.0, state_age_hours=2.0
        )
    ]
    current = [
        health(
            "A", status=cr.STATUS_OK, period_hours=24, stale_after_hours=36.0, state_age_hours=30.0
        )
    ]

    (change,) = registry.diff(previous, current)

    assert change.kind == cr.CHANGE_AGE
    assert change.level == cr.LEVEL_INFO
    assert "fresh → aging" in change.detail


def test_diff_against_empty_previous_marks_everything_appeared() -> None:
    registry = cr.CronRegistry(registry_path=Path("unused.json"), workspace_root=Path("."))
    current = [health("A", status=cr.STATUS_OK), health("B", status=cr.STATUS_FAILING)]

    changes = registry.diff([], current)

    assert {c.kind for c in changes} == {cr.CHANGE_APPEARED}
    assert changes[0].name == "B"  # severity first


# --------------------------------------------------------------------------- #
# validate_against_schtasks()
# --------------------------------------------------------------------------- #


def test_validate_finds_extra_and_missing_tasks(tmp_path: Path) -> None:
    registry = build(
        tmp_path,
        [
            {"name": "HF-Trending-Daily", "period_hours": 24, "state_file": "data/a.json"},
            {"name": "Ghost-Task", "period_hours": 24, "state_file": "data/b.json"},
            {"name": "Ollama AI Server", "period_hours": 1, "state_file": "data/c.json"},
        ],
    )

    messages = registry.validate_against_schtasks(run_command=fake_schtasks(SCHTASKS_CSV))

    joined = "\n".join(messages)
    assert "[missing] реестр ссылается на несуществующую задачу: 'Ghost-Task'" in joined
    assert "[extra] задача вне реестра: '\\Some-Other-Task'" in joined
    assert "[name]" in joined and "Ollama AI server" in joined
    assert "HF-Trending-Daily" not in joined  # exact match on both sides
    assert "UpdateOrchestrator" not in joined  # \Microsoft\ prefix is ignored


def test_validate_clean_registry_returns_no_messages(tmp_path: Path) -> None:
    registry = build(
        tmp_path,
        [
            {"name": "HF-Trending-Daily", "period_hours": 24, "state_file": "data/a.json"},
            {"name": "Ollama AI server", "period_hours": 1, "state_file": "data/b.json"},
            {"name": "Some-Other-Task", "period_hours": 24, "state_file": "data/c.json"},
        ],
    )

    assert registry.validate_against_schtasks(run_command=fake_schtasks(SCHTASKS_CSV)) == []


def test_validate_reports_schtasks_failure_without_crashing(tmp_path: Path) -> None:
    registry = build(tmp_path, [{"name": "A", "period_hours": 24, "state_file": "data/a.json"}])

    def missing_binary(cmd: Sequence[str], **kwargs: Any):
        raise FileNotFoundError("schtasks")

    messages = registry.validate_against_schtasks(run_command=missing_binary)

    assert len(messages) == 1
    assert messages[0].startswith("[error]")


def test_validate_handles_nonzero_schtasks_exit(tmp_path: Path) -> None:
    registry = build(tmp_path, [{"name": "A", "period_hours": 24, "state_file": "data/a.json"}])

    messages = registry.validate_against_schtasks(
        run_command=fake_schtasks("", returncode=1)
    )

    assert messages[0].startswith("[error]")


def test_parse_schtasks_csv_skips_repeated_headers() -> None:
    csv_text = SCHTASKS_CSV + '"HostName","TaskName","Next Run Time"\n"PC","\\Tail-Task","N/A"\n'

    names = cr.parse_schtasks_csv(csv_text)

    assert "\\Tail-Task" in names
    assert "TaskName" not in names


def test_parse_schtasks_csv_localized_header_falls_back_to_column_one() -> None:
    csv_text = (
        '"Имя узла","Имя задачи","Время следующего запуска"\n'
        '"PC","\\Локальная-Задача","N/A"\n'
    )

    assert cr.parse_schtasks_csv(csv_text) == ["\\Локальная-Задача"]


# --------------------------------------------------------------------------- #
# render_digest()
# --------------------------------------------------------------------------- #


def test_render_digest_sorts_failing_above_stale(tmp_path: Path) -> None:
    registry = build(tmp_path, [{"name": "A", "period_hours": 24, "state_file": "data/a.json"}])
    changes = [
        cr.HealthChange(
            name="Stale-Task",
            kind=cr.CHANGE_STATUS,
            level=cr.LEVEL_ALERT,
            previous_status=cr.STATUS_OK,
            current_status=cr.STATUS_STALE,
            detail="state не обновлялся 40ч",
            health=health("Stale-Task", status=cr.STATUS_STALE),
        ),
        cr.HealthChange(
            name="Failing-Task",
            kind=cr.CHANGE_STATUS,
            level=cr.LEVEL_ALERT,
            previous_status=cr.STATUS_OK,
            current_status=cr.STATUS_FAILING,
            detail="last_status=error",
            health=health("Failing-Task", status=cr.STATUS_FAILING),
        ),
        cr.HealthChange(
            name="Info-Task",
            kind=cr.CHANGE_APPEARED,
            level=cr.LEVEL_INFO,
            current_status=cr.STATUS_OK,
            detail="новая задача",
            health=health("Info-Task", status=cr.STATUS_OK),
        ),
    ]

    digest = registry.render_digest(changes, now=NOW)

    assert digest.splitlines()[0] == "🗂 Реестр cron-задач — 2026-10-21 12:00 UTC"
    assert "Изменений: 3 (alert: 2, info: 1)" in digest
    positions = [digest.index(name) for name in ("Failing-Task", "Stale-Task", "Info-Task")]
    assert positions == sorted(positions)


def test_render_digest_without_changes_says_all_good(tmp_path: Path) -> None:
    registry = build(tmp_path, [{"name": "A", "period_hours": 24, "state_file": "data/a.json"}])

    digest = registry.render_digest([], now=NOW)

    assert "Всё штатно" in digest
    assert "Изменений:" not in digest


def test_render_digest_appends_validation_block(tmp_path: Path) -> None:
    registry = build(tmp_path, [{"name": "A", "period_hours": 24, "state_file": "data/a.json"}])

    digest = registry.render_digest([], now=NOW, validation=["[extra] задача вне реестра: 'X'"])

    assert "Проверка реестра против schtasks:" in digest
    assert "задача вне реестра" in digest


def test_render_digest_shows_state_details(tmp_path: Path) -> None:
    write_state(tmp_path, "data/a.json", {"last_status": "error"}, age_hours=40)
    registry = build(tmp_path, [{"name": "A", "period_hours": 24, "state_file": "data/a.json"}])
    current = registry.check(NOW)
    changes = registry.diff([health("A", status=cr.STATUS_OK)], current)

    digest = registry.render_digest(changes, now=NOW)

    assert "data/a.json" in digest
    assert "порог" in digest


# --------------------------------------------------------------------------- #
# run() / CLI
# --------------------------------------------------------------------------- #


class RecordingSender:
    def __init__(self) -> None:
        self.messages: list[str] = []

    def send_message(self, text: str) -> dict[str, Any]:
        self.messages.append(text)
        return {"ok": True}


def test_run_sends_only_when_something_changed(tmp_path: Path) -> None:
    write_state(tmp_path, "data/a.json", {"last_status": "ok"}, age_hours=1)
    registry = build(tmp_path, [{"name": "A", "period_hours": 24, "state_file": "data/a.json"}])
    sender = RecordingSender()
    output: list[str] = []

    first = registry.run(
        validate=False, now=NOW, sender=sender, printer=output.append
    )
    second = registry.run(
        validate=False, now=NOW, sender=sender, printer=output.append
    )

    assert first == 0 and second == 0
    assert len(sender.messages) == 1  # only the first run saw a change (task appeared)
    assert "Изменений нет" in "\n".join(output)


def test_run_exit_code_1_on_stale(tmp_path: Path) -> None:
    write_state(tmp_path, "data/a.json", {"last_status": "ok"}, age_hours=100)
    registry = build(tmp_path, [{"name": "A", "period_hours": 24, "state_file": "data/a.json"}])

    assert registry.run(validate=False, send=False, now=NOW, printer=lambda _: None) == 1


def test_run_exit_code_0_when_only_info_statuses(tmp_path: Path) -> None:
    registry = build(
        tmp_path, [{"name": "A", "period_hours": 24, "state_file": "data/absent.json"}]
    )

    assert registry.run(validate=False, send=False, now=NOW, printer=lambda _: None) == 0


def test_run_exit_code_2_on_broken_registry(tmp_path: Path) -> None:
    path = tmp_path / "data" / "cron_registry.json"
    path.parent.mkdir(parents=True)
    path.write_text("{not json", encoding="utf-8")
    registry = cr.CronRegistry(registry_path=path, workspace_root=tmp_path)
    output: list[str] = []

    assert registry.run(now=NOW, printer=output.append) == 2
    assert output and output[0].startswith("ОШИБКА:")


def test_run_writes_snapshot_next_to_registry(tmp_path: Path) -> None:
    write_state(tmp_path, "data/a.json", {"ok": True}, age_hours=1)
    registry = build(tmp_path, [{"name": "A", "period_hours": 24, "state_file": "data/a.json"}])

    registry.run(validate=False, send=False, now=NOW, printer=lambda _: None)

    snapshot = tmp_path / "data" / "cron_registry_last.json"
    payload = json.loads(snapshot.read_text(encoding="utf-8"))
    assert payload["tasks"][0]["name"] == "A"
    assert payload["checked_at"] == "2026-10-21T12:00:00+00:00"


def test_run_table_lists_every_task(tmp_path: Path) -> None:
    write_state(tmp_path, "data/a.json", {"ok": True}, age_hours=1)
    registry = build(
        tmp_path,
        [
            {"name": "A", "period_hours": 24, "state_file": "data/a.json"},
            {"name": "B", "period_hours": 24, "state_file": "data/absent.json"},
        ],
    )
    output: list[str] = []

    registry.run(check=True, diff=False, validate=False, send=False, now=NOW, printer=output.append)

    text = "\n".join(output)
    rows = [line.split()[0] for line in text.splitlines() if line and not line.startswith(("-", "Реестр"))]
    assert "TASK" in rows
    assert {"A", "B"} <= set(rows)
    assert "missing_state" in text
    assert "ok" in text


def test_run_validation_uses_schtasks_and_marks_unknown_task(tmp_path: Path) -> None:
    write_state(tmp_path, "data/a.json", {"ok": True}, age_hours=1)
    registry = build(
        tmp_path, [{"name": "Ghost-Task", "period_hours": 24, "state_file": "data/a.json"}]
    )
    output: list[str] = []

    exit_code = registry.run(
        send=False,
        now=NOW,
        run_command=fake_schtasks(SCHTASKS_CSV),
        printer=output.append,
    )

    text = "\n".join(output)
    assert exit_code == 0
    assert "несуществующую задачу" in text
    assert cr.STATUS_UNKNOWN_TASK in text


def test_run_corrupt_snapshot_is_ignored(tmp_path: Path) -> None:
    write_state(tmp_path, "data/a.json", {"ok": True}, age_hours=1)
    registry = build(tmp_path, [{"name": "A", "period_hours": 24, "state_file": "data/a.json"}])
    (tmp_path / "data" / "cron_registry_last.json").write_text("{broken", encoding="utf-8")

    assert registry.load_last_run() == []
    assert registry.run(validate=False, send=False, now=NOW, printer=lambda _: None) == 0


def test_send_digest_reports_sender_failure() -> None:
    class Boom:
        def send_message(self, text: str) -> dict[str, Any]:
            raise RuntimeError("telegram down")

    result = cr.CronRegistry.send_digest("text", sender=Boom())

    assert result == {"ok": False, "error": "telegram down"}


def test_send_digest_truncates_long_messages() -> None:
    sender = RecordingSender()

    cr.CronRegistry.send_digest("x" * 9000, sender=sender)

    assert len(sender.messages[0]) <= cr.TELEGRAM_TEXT_LIMIT
    assert sender.messages[0].endswith("(обрезано)")


@pytest.mark.parametrize(
    "argv, expected",
    [
        (["--check", "--no-send"], {"check": True, "diff": False, "validate": False, "send": False}),
        (["--validate"], {"check": False, "diff": False, "validate": True, "send": False}),
        (["--send"], {"check": True, "diff": True, "validate": True, "send": True}),
        (["--send", "--no-send"], {"check": True, "diff": True, "validate": True, "send": False}),
    ],
)
def test_resolve_stages(argv: list[str], expected: dict[str, bool]) -> None:
    args = cr.build_parser().parse_args(argv)

    assert cr.resolve_stages(args) == expected


# --------------------------------------------------------------------------- #
# The shipped registry
# --------------------------------------------------------------------------- #


def test_shipped_registry_is_valid() -> None:
    registry = cr.CronRegistry()
    loaded = registry.load()

    assert loaded.version == 1
    assert len(loaded.tasks) == 6
    for task in loaded.tasks:
        assert task.period_hours > 0
        # A task without a state/log artifact is still valid: its liveness is
        # then taken from the Task Scheduler's own exit code.
        assert task.state_file or task.log_file or task.command


def test_shipped_registry_check_does_not_touch_real_state(tmp_path: Path) -> None:
    registry = cr.CronRegistry(workspace_root=tmp_path)
    registry.registry_path = cr.DEFAULT_REGISTRY_PATH
    registry.last_run_path = tmp_path / "data" / "cron_registry_last.json"

    report = registry.check(NOW)

    assert len(report) == 6
    # The real workspace state must not leak into the report: every task is
    # judged against the empty tmp_path. A task that has no state/log file
    # at all has nothing to observe, so it is reported as unknown_task.
    for item in report:
        assert item.status in (
            cr.STATUS_MISSING_STATE,
            cr.STATUS_UNKNOWN_TASK,
            cr.STATUS_OK,
            cr.STATUS_FAILING,
            cr.STATUS_STALE,
        )
        assert item.state_path is None or str(item.state_path).startswith(
            str(tmp_path)
        ), f"{item.name} leaked a real state path: {item.state_path}"


# --------------------------------------------------------------------------- #
# Windows-specific behaviour: console code page and scheduler ground truth
# --------------------------------------------------------------------------- #


def test_schtasks_output_is_decoded_from_the_console_code_page() -> None:
    """schtasks writes OEM (cp866 on RU Windows), not UTF-8.

    Reading it with ``text=True`` raises UnicodeDecodeError and the whole
    registry fails with "schtasks не вернул ни одной задачи".
    """
    raw = "Имя задачи,Прошлый результат\r\nCursor-CloudAgent-Poll,0\r\n".encode("cp866")
    assert cr.decode_schtasks_output(raw).startswith("Имя задачи")

    # A str passes through unchanged.
    assert cr.decode_schtasks_output("already text") == "already text"


def test_schtasks_facts_expose_last_run_and_exit_code() -> None:
    csv_text = (
        "Имя узла,Имя задачи,Время следующего запуска,Состояние,"
        "Время прошлого запуска,Прошлый результат\r\n"
        "HOST,A-Task,21.10.2026 10:00:00,Готово,21.10.2026 9:00:00,0\r\n"
        "HOST,B-Task,21.10.2026 10:00:00,Готово,21.10.2026 9:00:00,1\r\n"
    )
    facts = cr.parse_schtasks_facts(csv_text)

    assert set(facts) == {"a-task", "b-task"}
    assert facts["a-task"].last_result == 0
    assert facts["b-task"].last_result == 1
    assert facts["b-task"].last_run == "21.10.2026 9:00:00"


def test_scheduled_failure_marks_the_task_failing(tmp_path: Path) -> None:
    """A non-zero exit code from the scheduler is a failure signal.

    Most OpenClaw tasks never write a state file, so the scheduler's own
    exit code is the only evidence that they are alive.
    """
    registry = build(
        tmp_path,
        [{"name": "A", "period_hours": 24, "state_file": "data/a.json"}],
    )
    facts = {"a": cr.SchtaskFact(name="A", last_result=1, last_run="21.10.2026 9:00:00")}

    health = registry.check(NOW, facts=facts)[0]

    assert health.status == cr.STATUS_FAILING
    assert health.scheduled_last_result == 1
    assert "1" in health.detail


def test_scheduled_success_is_not_a_missing_state_failure(tmp_path: Path) -> None:
    """A missing state file is not a problem when the scheduler confirms the run."""
    registry = build(
        tmp_path,
        [{"name": "A", "period_hours": 24, "state_file": "data/absent.json"}],
    )
    facts = {"a": cr.SchtaskFact(name="A", last_result=0, last_run="21.10.2026 9:00:00")}

    health = registry.check(NOW, facts=facts)[0]

    assert health.status != cr.STATUS_FAILING
    assert health.scheduled_last_result == 0
