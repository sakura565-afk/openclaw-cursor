#!/usr/bin/env python3
"""Single registry of scheduled tasks with a liveness ("did it even run?") check.

The registry (``data/cron_registry.json``) lists every Scheduled Task we own,
its state file, its log file and the period it is expected to run at. The check
answers the question the per-task alerts cannot: *has this task gone quiet?*

A task is ``stale`` when its state file has not been refreshed for longer than
``period_hours * stale_factor`` (1.5 by default) — i.e. it missed more than one
and a half runs — regardless of whether the last recorded run succeeded.

Digest delivery is diff-based: only tasks whose health actually changed since
the previous run are reported, so a quiet machine sends nothing.

CLI::

    python -m scripts.cron_registry --check --no-send
    python -m scripts.cron_registry --validate
    python -m scripts.cron_registry --send

Exit codes: ``0`` all good / info-only, ``1`` something is stale or failing,
``2`` the registry itself is broken.
"""

from __future__ import annotations

import argparse
import csv
import io
import json
import os
import re
import subprocess
import sys
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Iterable, Sequence

try:
    from scripts.windows_console import decode_console_output
except ImportError:  # pragma: no cover - direct script execution
    import sys as _sys
    from pathlib import Path as _Path
    _sys.path.insert(0, str(_Path(__file__).resolve().parent))
    from windows_console import decode_console_output

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

DEFAULT_REGISTRY_PATH = ROOT / "data" / "cron_registry.json"
DEFAULT_LAST_RUN_NAME = "cron_registry_last.json"
DEFAULT_PERIOD_HOURS = 24.0
DEFAULT_STALE_FACTOR = 1.5
DEFAULT_IGNORE_PREFIXES: tuple[str, ...] = ("\\Microsoft\\",)
TELEGRAM_TEXT_LIMIT = 4000
LOG_TAIL_BYTES = 8192

STATUS_OK = "ok"
STATUS_STALE = "stale"
STATUS_FAILING = "failing"
STATUS_MISSING_STATE = "missing_state"
STATUS_DISABLED = "disabled"
STATUS_UNKNOWN_TASK = "unknown_task"

# failing > stale > missing_state > unknown_task > ok
SEVERITY: dict[str, int] = {
    STATUS_FAILING: 4,
    STATUS_STALE: 3,
    STATUS_MISSING_STATE: 2,
    STATUS_UNKNOWN_TASK: 1,
    STATUS_OK: 0,
    STATUS_DISABLED: 0,
}
ALERT_STATUSES = frozenset({STATUS_FAILING, STATUS_STALE})

STATUS_ICONS = {
    STATUS_FAILING: "🔴",
    STATUS_STALE: "🟠",
    STATUS_MISSING_STATE: "🟡",
    STATUS_UNKNOWN_TASK: "⚪",
    STATUS_OK: "🟢",
    STATUS_DISABLED: "⚫",
}

CHANGE_APPEARED = "appeared"
CHANGE_DISAPPEARED = "disappeared"
CHANGE_STATUS = "status_changed"
CHANGE_AGE = "age_changed"

LEVEL_ALERT = "alert"
LEVEL_INFO = "info"

LAST_STATUS_KEYS: tuple[str, ...] = (
    "last_status",
    "status",
    "state",
    "result",
    "health",
    "outcome",
)
LAST_SUCCESS_KEYS: tuple[str, ...] = (
    "last_success",
    "last_success_at",
    "last_ok",
    "last_ok_at",
    "last_run",
    "last_run_at",
    "generated_at",
    "updated_at",
    "timestamp",
    "date_utc",
)
FAILURE_WORDS = frozenset(
    {
        "error",
        "errors",
        "err",
        "fail",
        "failed",
        "failing",
        "failure",
        "crash",
        "crashed",
        "down",
        "dead",
        "broken",
        "unhealthy",
        "critical",
        "timeout",
    }
)
SUCCESS_WORDS = frozenset(
    {
        "ok",
        "okay",
        "up",
        "success",
        "succeeded",
        "healthy",
        "pass",
        "passed",
        "done",
        "idle",
        "running",
        "completed",
    }
)
LOG_EXIT_RE = re.compile(r"exit[=: ]\s*(-?\d+)", re.IGNORECASE)
GLOB_CHARS = ("*", "?", "[")


class RegistryError(RuntimeError):
    """Raised when ``cron_registry.json`` is missing, unreadable or invalid."""


class SchtasksError(RuntimeError):
    """Raised when the ``schtasks`` query cannot be executed or parsed."""


# --------------------------------------------------------------------------- #
# Data model
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class TaskSpec:
    name: str
    enabled: bool = True
    command: str = ""
    schedule: str = ""
    period_hours: float = DEFAULT_PERIOD_HOURS
    state_file: str = ""
    log_file: str = ""
    alert: str = "telegram"
    notes: str = ""
    stale_factor: float | None = None

    def stale_after_hours(self, default_factor: float = DEFAULT_STALE_FACTOR) -> float:
        factor = self.stale_factor if self.stale_factor else default_factor
        return float(self.period_hours) * float(factor)


@dataclass(frozen=True)
class Registry:
    version: int = 1
    stale_factor: float = DEFAULT_STALE_FACTOR
    ignore_task_prefixes: tuple[str, ...] = DEFAULT_IGNORE_PREFIXES
    tasks: tuple[TaskSpec, ...] = ()

    def task_names(self) -> tuple[str, ...]:
        return tuple(task.name for task in self.tasks)


@dataclass(frozen=True)
class SchtaskFact:
    """What Windows itself reports about a scheduled task.

    ``schtasks`` knows the real last-run time and the real exit code — this is
    the ground truth. The registry's state files are only a secondary signal,
    because most of the OpenClaw tasks never write one.
    """

    name: str
    last_run: str | None = None
    last_result: int | None = None
    status: str | None = None
    next_run: str | None = None


@dataclass
class TaskHealth:
    name: str
    status: str = STATUS_UNKNOWN_TASK
    enabled: bool = True
    period_hours: float = DEFAULT_PERIOD_HOURS
    stale_after_hours: float = DEFAULT_PERIOD_HOURS * DEFAULT_STALE_FACTOR
    state_pattern: str = ""
    state_path: str | None = None
    state_age_hours: float | None = None
    state_mtime: str | None = None
    last_status: str | None = None
    last_success: str | None = None
    log_pattern: str = ""
    log_path: str | None = None
    log_age_hours: float | None = None
    log_last_line: str | None = None
    log_exit_code: int | None = None
    # Ground truth straight from Windows Task Scheduler.
    scheduled_last_run: str | None = None
    scheduled_last_result: int | None = None
    scheduled_status: str | None = None
    detail: str = ""

    @property
    def severity(self) -> int:
        return SEVERITY.get(self.status, 0)

    @property
    def age_hours(self) -> float | None:
        """Age of the freshest liveness signal (state file, else log file)."""
        if self.state_age_hours is not None:
            return self.state_age_hours
        return self.log_age_hours

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["severity"] = self.severity
        return payload

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> "TaskHealth":
        allowed = {f for f in cls.__dataclass_fields__}  # noqa: SLF001 - dataclass API
        data = {key: value for key, value in (payload or {}).items() if key in allowed}
        if not data.get("name"):
            raise ValueError("task health entry without a name")
        return cls(**data)


@dataclass
class HealthChange:
    name: str
    kind: str
    level: str = LEVEL_INFO
    previous_status: str | None = None
    current_status: str | None = None
    detail: str = ""
    health: TaskHealth | None = None

    @property
    def severity(self) -> int:
        return SEVERITY.get(self.current_status or self.previous_status or STATUS_OK, 0)

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "kind": self.kind,
            "level": self.level,
            "previous_status": self.previous_status,
            "current_status": self.current_status,
            "detail": self.detail,
            "severity": self.severity,
        }


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _iso(moment: datetime) -> str:
    return moment.astimezone(timezone.utc).replace(microsecond=0).isoformat()


def _format_hours(value: float | None) -> str:
    if value is None:
        return "-"
    if value < 1:
        return f"{value * 60:.0f}м"
    if value < 48:
        return f"{value:.1f}ч"
    return f"{value / 24:.1f}д"


def _normalize_task_name(name: str) -> str:
    text = (name or "").strip().strip('"')
    text = text.lstrip("\\")
    text = re.sub(r"\s+", " ", text)
    return text.casefold()


def _coerce_float(value: Any) -> float | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str):
        try:
            return float(value.strip())
        except ValueError:
            return None
    return None


def _normalize_status_word(value: str) -> str:
    return re.sub(r"[^a-zа-я0-9]+", "", (value or "").strip().casefold())


def _is_failure_status(value: str | None) -> bool:
    word = _normalize_status_word(value or "")
    if not word:
        return False
    if word in SUCCESS_WORDS:
        return False
    if word in FAILURE_WORDS:
        return True
    return any(bad in word for bad in ("error", "fail", "crash", "unhealthy", "timeout"))


def extract_last_status(payload: Any) -> str | None:
    """Pull a human-readable ``last_status`` out of an arbitrary state payload."""
    if not isinstance(payload, dict):
        return None
    for key in LAST_STATUS_KEYS:
        value = payload.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
        if isinstance(value, bool):
            return STATUS_OK if value else "error"
    if isinstance(payload.get("ok"), bool):
        return STATUS_OK if payload["ok"] else "error"
    for key in ("alert_failure_count", "fail_count", "error_count", "failures"):
        count = payload.get(key)
        if isinstance(count, int) and not isinstance(count, bool) and count > 0:
            return f"error ({key}={count})"
    error = payload.get("error") or payload.get("last_error")
    if isinstance(error, str) and error.strip():
        return f"error: {error.strip()[:120]}"
    return None


def extract_last_success(payload: Any) -> str | None:
    if not isinstance(payload, dict):
        return None
    for key in LAST_SUCCESS_KEYS:
        value = payload.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
        if isinstance(value, (int, float)) and not isinstance(value, bool):
            try:
                return _iso(datetime.fromtimestamp(float(value), timezone.utc))
            except (OverflowError, OSError, ValueError):
                return str(value)
    return None


def parse_schtasks_csv(csv_text: str) -> list[str]:
    """Return task names from ``schtasks /Query /FO CSV /V`` output.

    ``/V`` repeats the header row for every folder and the header is localized
    on non-English Windows, so the ``TaskName`` column is located by name when
    possible and falls back to column index 1 (``HostName`` is always first).
    """
    names: list[str] = []
    seen: set[str] = set()
    column = 1
    header: list[str] | None = None
    reader = csv.reader(io.StringIO(csv_text or ""))
    for row in reader:
        if not row:
            continue
        cells = [cell.strip() for cell in row]
        lowered = [cell.casefold() for cell in cells]
        if "taskname" in lowered:
            column = lowered.index("taskname")
            header = header or lowered
            continue
        if header is None:
            # Localized header (e.g. "Имя задачи"): HostName is always first.
            header = lowered
            column = min(1, len(cells) - 1)
            continue
        if lowered == header:
            continue
        if column >= len(cells):
            continue
        name = cells[column]
        if not name:
            continue
        key = _normalize_task_name(name)
        if key and key not in seen:
            seen.add(key)
            names.append(name)
    return names


# ``schtasks`` writes its output in the console OEM code page (cp866 on a
# Russian Windows), NOT in UTF-8. Reading it with ``text=True`` (utf-8 by
# default) raises UnicodeDecodeError and the whole registry fails with
# "schtasks не вернул ни одной задачи". The output is therefore captured as
# bytes and decoded explicitly, trying the likely code pages in order.
SCHTASKS_ENCODINGS: tuple[str, ...] = ("cp866", "cp1251", "cp437", "utf-8")


def decode_schtasks_output(raw: bytes | str) -> str:
    """Decode raw ``schtasks`` bytes using the console OEM code page."""
    return decode_console_output(raw)


def query_scheduled_tasks(
    *,
    run_command: Callable[..., subprocess.CompletedProcess[str]] = subprocess.run,
    timeout: float = 120.0,
) -> list[str]:
    """Run ``schtasks /Query /FO CSV /V`` and return the task names it reports."""
    cmd = ["schtasks", "/Query", "/FO", "CSV", "/V"]
    try:
        completed = run_command(
            cmd,
            capture_output=True,
            check=False,
            timeout=timeout,
        )
    except FileNotFoundError as exc:
        raise SchtasksError(f"schtasks не найден: {exc}") from exc
    except (OSError, subprocess.SubprocessError) as exc:
        raise SchtasksError(f"не удалось выполнить schtasks: {exc}") from exc

    stdout = decode_schtasks_output(getattr(completed, "stdout", b"") or b"")
    stderr = decode_schtasks_output(getattr(completed, "stderr", b"") or b"")

    if completed.returncode != 0:
        detail = (stderr or stdout).strip()[:300]
        raise SchtasksError(f"schtasks вернул код {completed.returncode}: {detail or 'без вывода'}")

    names = parse_schtasks_csv(stdout)
    if not names:
        raise SchtasksError("schtasks не вернул ни одной задачи (пустой CSV)")
    return names



# Localized schtasks CSV headers (Russian Windows) and the English ones.
# The columns are identified by substring match so the same code works on
# both locales without a translation table.
_SCHTASKS_COLUMN_KEYS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("name", ("имя задачи", "taskname", "task name")),
    ("status", ("состояние", "status")),
    ("last_run", ("время прошлого запуска", "last run time", "last run")),
    ("last_result", ("прошлый результат", "last result")),
    ("next_run", ("время следующего запуска", "next run time", "next run")),
)


def _column_index(header: list[str], keys: tuple[str, ...]) -> int | None:
    lowered = [cell.casefold() for cell in header]
    for key in keys:
        for index, cell in enumerate(lowered):
            if key in cell:
                return index
    return None


def parse_schtasks_facts(csv_text: str) -> dict[str, SchtaskFact]:
    """Parse ``schtasks /Query /FO CSV /V`` into per-task ground-truth facts.

    Unlike :func:`parse_schtasks_csv` this keeps the last-run time, the exit
    code and the scheduler state, which is the only reliable "is this task
    alive?" signal for tasks that never write a state file.
    """
    reader = csv.reader(io.StringIO(csv_text or ""))
    facts: dict[str, SchtaskFact] = {}
    header: list[str] | None = None
    indexes: dict[str, int | None] = {}
    for row in reader:
        if not row:
            continue
        cells = [cell.strip().strip('"') for cell in row]
        lowered = [cell.casefold() for cell in cells]
        if "taskname" in lowered or "имя задачи" in lowered:
            header = lowered
            indexes = {
                key: _column_index(header, keys) for key, keys in _SCHTASKS_COLUMN_KEYS
            }
            continue
        if header is None:
            header = lowered
            indexes = {
                key: _column_index(header, keys) for key, keys in _SCHTASKS_COLUMN_KEYS
            }
            continue
        if lowered == header:
            continue

        name_index = indexes.get("name")
        if name_index is None or name_index >= len(cells):
            continue
        name = cells[name_index]
        if not name:
            continue
        key = _normalize_task_name(name)
        if not key or key in facts:
            continue

        def _cell(field: str) -> str | None:
            index = indexes.get(field)
            if index is None or index >= len(cells):
                return None
            value = cells[index].strip()
            return value or None

        result_text = _cell("last_result")
        result_code: int | None = None
        if result_text is not None:
            try:
                result_code = int(result_text)
            except ValueError:
                result_code = None

        facts[key] = SchtaskFact(
            name=name,
            last_run=_cell("last_run"),
            last_result=result_code,
            status=_cell("status"),
            next_run=_cell("next_run"),
        )
    return facts


def query_schtasks(
    *,
    run_command: Callable[..., subprocess.CompletedProcess[str]] = subprocess.run,
    timeout: float = 120.0,
) -> tuple[list[str], dict[str, SchtaskFact]]:
    """Query Windows Task Scheduler once and return names + ground-truth facts.

    A single ``schtasks`` call is enough for both: the names tell whether the
    registry entry still exists, the facts tell when it last ran and with
    which exit code.
    """
    cmd = ["schtasks", "/Query", "/FO", "CSV", "/V"]
    try:
        completed = run_command(cmd, capture_output=True, check=False, timeout=timeout)
    except FileNotFoundError as exc:
        raise SchtasksError(f"schtasks не найден: {exc}") from exc
    except (OSError, subprocess.SubprocessError) as exc:
        raise SchtasksError(f"не удалось выполнить schtasks: {exc}") from exc

    stdout = decode_schtasks_output(getattr(completed, "stdout", b"") or b"")
    stderr = decode_schtasks_output(getattr(completed, "stderr", b"") or b"")

    if completed.returncode != 0:
        detail = (stderr or stdout).strip()[:300]
        raise SchtasksError(f"schtasks вернул код {completed.returncode}: {detail or 'без вывода'}")

    names = parse_schtasks_csv(stdout)
    facts = parse_schtasks_facts(stdout)
    if not names and not facts:
        raise SchtasksError("schtasks не вернул ни одной задачи (пустой CSV)")
    return names, facts


def query_scheduled_task_facts(
    *,
    run_command: Callable[..., subprocess.CompletedProcess[str]] = subprocess.run,
    timeout: float = 120.0,
) -> dict[str, SchtaskFact]:
    """Run ``schtasks`` and return the ground truth for every task it reports."""
    cmd = ["schtasks", "/Query", "/FO", "CSV", "/V"]
    try:
        completed = run_command(cmd, capture_output=True, check=False, timeout=timeout)
    except FileNotFoundError as exc:
        raise SchtasksError(f"schtasks не найден: {exc}") from exc
    except (OSError, subprocess.SubprocessError) as exc:
        raise SchtasksError(f"не удалось выполнить schtasks: {exc}") from exc

    stdout = decode_schtasks_output(getattr(completed, "stdout", b"") or b"")
    stderr = decode_schtasks_output(getattr(completed, "stderr", b"") or b"")

    if completed.returncode != 0:
        detail = (stderr or stdout).strip()[:300]
        raise SchtasksError(f"schtasks вернул код {completed.returncode}: {detail or 'без вывода'}")

    facts = parse_schtasks_facts(stdout)
    if not facts:
        raise SchtasksError("schtasks не вернул ни одной задачи (пустой CSV)")
    return facts


# --------------------------------------------------------------------------- #
# Registry
# --------------------------------------------------------------------------- #


class CronRegistry:
    """Loads the registry, checks task liveness and renders a diff digest."""

    def __init__(
        self,
        registry_path: os.PathLike[str] | str | None = None,
        workspace_root: os.PathLike[str] | str | None = None,
        *,
        last_run_path: os.PathLike[str] | str | None = None,
    ) -> None:
        self.workspace_root = Path(workspace_root) if workspace_root else ROOT
        self.registry_path = (
            Path(registry_path)
            if registry_path
            else self.workspace_root / "data" / "cron_registry.json"
        )
        self.last_run_path = (
            Path(last_run_path)
            if last_run_path
            else self.registry_path.parent / DEFAULT_LAST_RUN_NAME
        )
        self._registry: Registry | None = None

    # -- loading ----------------------------------------------------------- #

    def load(self, *, force: bool = False) -> Registry:
        """Parse and validate the registry; raises :class:`RegistryError`."""
        if self._registry is not None and not force:
            return self._registry

        path = self.registry_path
        if not path.is_file():
            raise RegistryError(f"Реестр cron-задач не найден: {path}")
        try:
            raw = path.read_text(encoding="utf-8")
        except OSError as exc:
            raise RegistryError(f"Не удалось прочитать реестр {path}: {exc}") from exc
        try:
            payload = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise RegistryError(
                f"Реестр {path} — битый JSON: {exc.msg} "
                f"(строка {exc.lineno}, колонка {exc.colno})"
            ) from exc

        registry = self._validate(payload, path)
        self._registry = registry
        return registry

    @staticmethod
    def _validate(payload: Any, path: Path) -> Registry:
        problems: list[str] = []
        if not isinstance(payload, dict):
            raise RegistryError(
                f"Реестр {path} повреждён: корневой элемент должен быть объектом JSON, "
                f"получен {type(payload).__name__}"
            )

        version = payload.get("version", 1)
        if not isinstance(version, int) or isinstance(version, bool) or version < 1:
            problems.append(f"version: ожидалось целое число >= 1, получено {version!r}")
            version = 1

        stale_factor = _coerce_float(payload.get("stale_factor", DEFAULT_STALE_FACTOR))
        if stale_factor is None or stale_factor <= 0:
            problems.append(
                f"stale_factor: ожидалось число > 0, получено {payload.get('stale_factor')!r}"
            )
            stale_factor = DEFAULT_STALE_FACTOR

        prefixes_raw = payload.get("ignore_task_prefixes", list(DEFAULT_IGNORE_PREFIXES))
        prefixes: list[str] = []
        if isinstance(prefixes_raw, list):
            for item in prefixes_raw:
                if isinstance(item, str) and item.strip():
                    prefixes.append(item.strip())
                else:
                    problems.append(f"ignore_task_prefixes: ожидались строки, получено {item!r}")
        else:
            problems.append(
                f"ignore_task_prefixes: ожидался список строк, получено {prefixes_raw!r}"
            )

        tasks_raw = payload.get("tasks")
        if not isinstance(tasks_raw, list) or not tasks_raw:
            problems.append("tasks: ожидался непустой список задач")
            tasks_raw = []

        tasks: list[TaskSpec] = []
        seen: dict[str, int] = {}
        for index, item in enumerate(tasks_raw):
            where = f"tasks[{index}]"
            if not isinstance(item, dict):
                problems.append(f"{where}: ожидался объект, получен {type(item).__name__}")
                continue
            name = item.get("name")
            if not isinstance(name, str) or not name.strip():
                problems.append(f"{where}.name: обязательное непустое строковое поле")
                continue
            name = name.strip()
            key = _normalize_task_name(name)
            if key in seen:
                problems.append(f"{where}.name: дубликат задачи {name!r} (уже в tasks[{seen[key]}])")
                continue
            seen[key] = index

            period = _coerce_float(item.get("period_hours", DEFAULT_PERIOD_HOURS))
            if period is None or period <= 0:
                problems.append(
                    f"{where}.period_hours: ожидалось число > 0, получено "
                    f"{item.get('period_hours')!r}"
                )
                period = DEFAULT_PERIOD_HOURS

            enabled = item.get("enabled", True)
            if not isinstance(enabled, bool):
                problems.append(f"{where}.enabled: ожидался true/false, получено {enabled!r}")
                enabled = True

            task_factor = item.get("stale_factor")
            factor: float | None = None
            if task_factor is not None:
                factor = _coerce_float(task_factor)
                if factor is None or factor <= 0:
                    problems.append(
                        f"{where}.stale_factor: ожидалось число > 0, получено {task_factor!r}"
                    )
                    factor = None

            texts: dict[str, str] = {}
            for field_name in ("command", "schedule", "state_file", "log_file", "alert", "notes"):
                value = item.get(field_name, "")
                if value is None:
                    value = ""
                if not isinstance(value, str):
                    problems.append(
                        f"{where}.{field_name}: ожидалась строка, получено {value!r}"
                    )
                    value = ""
                texts[field_name] = value.strip()

            tasks.append(
                TaskSpec(
                    name=name,
                    enabled=enabled,
                    period_hours=period,
                    stale_factor=factor,
                    **texts,
                )
            )

        if problems:
            bullets = "\n".join(f"  - {line}" for line in problems)
            raise RegistryError(f"Реестр {path} повреждён:\n{bullets}")

        return Registry(
            version=version,
            stale_factor=stale_factor,
            ignore_task_prefixes=tuple(prefixes),
            tasks=tuple(tasks),
        )

    # -- path resolution --------------------------------------------------- #

    def resolve_pattern(self, pattern: str) -> Path | None:
        """Resolve a (possibly glob) path to the newest matching file by mtime."""
        raw = (pattern or "").strip()
        if not raw:
            return None
        candidate = Path(raw)
        if not candidate.is_absolute():
            candidate = self.workspace_root / raw
        try:
            if any(ch in raw for ch in GLOB_CHARS):
                matches = [p for p in candidate.parent.glob(candidate.name) if p.is_file()]
                if not matches:
                    return None
                return max(matches, key=lambda p: p.stat().st_mtime)
            return candidate if candidate.is_file() else None
        except OSError:
            return None

    @staticmethod
    def _age_hours(path: Path, now: datetime) -> tuple[float, datetime] | None:
        try:
            mtime = datetime.fromtimestamp(path.stat().st_mtime, timezone.utc)
        except OSError:
            return None
        age = (now - mtime).total_seconds() / 3600.0
        return max(0.0, age), mtime

    @staticmethod
    def _read_log_tail(path: Path) -> tuple[str | None, int | None]:
        """Return the last non-empty log line plus any ``exit=<code>`` it carries."""
        try:
            size = path.stat().st_size
            with path.open("rb") as handle:
                if size > LOG_TAIL_BYTES:
                    handle.seek(-LOG_TAIL_BYTES, os.SEEK_END)
                tail = handle.read()
        except OSError:
            return None, None
        text = tail.decode("utf-8", errors="replace")
        lines = [line.strip() for line in text.splitlines() if line.strip()]
        if not lines:
            return None, None
        exit_code: int | None = None
        for line in reversed(lines):
            match = LOG_EXIT_RE.search(line)
            if match:
                try:
                    exit_code = int(match.group(1))
                except ValueError:
                    exit_code = None
                break
        return lines[-1][:300], exit_code

    # -- checking ---------------------------------------------------------- #

    def check(
        self,
        now: datetime | None = None,
        *,
        system_tasks: Sequence[str] | None = None,
        registry: Registry | None = None,
        facts: dict[str, SchtaskFact] | None = None,
    ) -> list[TaskHealth]:
        """Compute health for every registry entry, sorted by severity."""
        reg = registry or self.load()
        moment = (now or utc_now()).astimezone(timezone.utc)
        known = (
            {_normalize_task_name(name) for name in system_tasks}
            if system_tasks is not None
            else None
        )
        results = [
            self._check_task(task, moment, known, reg.stale_factor, facts)
            for task in reg.tasks
        ]
        return sort_health(results)

    def _check_task(
        self,
        spec: TaskSpec,
        now: datetime,
        known_tasks: set[str] | None,
        default_factor: float,
        facts: dict[str, SchtaskFact] | None = None,
    ) -> TaskHealth:
        threshold = spec.stale_after_hours(default_factor)
        health = TaskHealth(
            name=spec.name,
            enabled=spec.enabled,
            period_hours=float(spec.period_hours),
            stale_after_hours=round(threshold, 4),
            state_pattern=spec.state_file,
            log_pattern=spec.log_file,
        )
        problems: list[str] = []

        # Ground truth from Windows Task Scheduler.
        fact = (facts or {}).get(_normalize_task_name(spec.name))
        if fact is not None:
            health.scheduled_last_run = fact.last_run
            health.scheduled_last_result = fact.last_result
            health.scheduled_status = fact.status

        state_path = self.resolve_pattern(spec.state_file)
        if state_path is not None:
            health.state_path = str(state_path)
            aged = self._age_hours(state_path, now)
            if aged is not None:
                health.state_age_hours = round(aged[0], 3)
                health.state_mtime = _iso(aged[1])
            payload = self._read_json(state_path)
            if payload is None:
                problems.append("state-файл не читается как JSON-объект")
            else:
                health.last_status = extract_last_status(payload)
                health.last_success = extract_last_success(payload)

        log_path = self.resolve_pattern(spec.log_file)
        if log_path is not None:
            health.log_path = str(log_path)
            aged = self._age_hours(log_path, now)
            if aged is not None:
                health.log_age_hours = round(aged[0], 3)
            health.log_last_line, health.log_exit_code = self._read_log_tail(log_path)

        if not spec.enabled:
            health.status = STATUS_DISABLED
            health.detail = "задача отключена в реестре"
            return health

        if known_tasks is not None and _normalize_task_name(spec.name) not in known_tasks:
            health.status = STATUS_UNKNOWN_TASK
            health.detail = "задачи нет в Scheduled Tasks (реестр ссылается на несуществующую)"
            return health

        if not spec.state_file and not spec.log_file and fact is None:
            health.status = STATUS_UNKNOWN_TASK
            health.detail = "в реестре не задан ни state_file, ни log_file — наблюдать нечего"
            return health

        state_failing = _is_failure_status(health.last_status)
        if state_failing:
            problems.append(f"state сообщает last_status={health.last_status}")
        log_failing = health.log_exit_code is not None and health.log_exit_code != 0
        if log_failing:
            problems.append(f"последний запуск в логе завершился с exit={health.log_exit_code}")

        # The scheduler's own exit code is the most reliable failure signal:
        # 0 = success, 1 = script error, 0x80070xxx = Windows-level failure.
        scheduled_failing = (
            health.scheduled_last_result is not None
            and health.scheduled_last_result != 0
        )
        if scheduled_failing:
            code = health.scheduled_last_result
            hexed = f"0x{code & 0xFFFFFFFF:08X}"
            problems.append(
                f"Scheduled Tasks: последний запуск завершился с кодом {code} ({hexed})"
            )

        age = health.age_hours
        is_stale = age is not None and age > threshold
        if is_stale:
            source = "state" if health.state_age_hours is not None else "лог"
            problems.append(
                f"{source} не обновлялся {_format_hours(age)} "
                f"(порог {_format_hours(threshold)} = {spec.period_hours} ч × "
                f"{spec.stale_factor or default_factor})"
            )

        if spec.state_file and state_path is None:
            if fact is not None:
                problems.append(
                    f"нет state-файла по шаблону {spec.state_file} "
                    "(но Scheduled Tasks подтверждает запуск)"
                )
            else:
                problems.append(f"нет state-файла по шаблону {spec.state_file}")

        if state_failing or log_failing or scheduled_failing:
            health.status = STATUS_FAILING
        elif spec.state_file and state_path is None and fact is None:
            # No state file is only a problem when the scheduler gives us
            # nothing to fall back on.
            health.status = STATUS_MISSING_STATE
        elif is_stale:
            health.status = STATUS_STALE
        else:
            health.status = STATUS_OK
            if not problems:
                problems.append(
                    f"свежесть {_format_hours(age)} при пороге {_format_hours(threshold)}"
                )

        health.detail = "; ".join(problems)
        return health

    @staticmethod
    def _read_json(path: Path) -> dict[str, Any] | None:
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError, UnicodeDecodeError):
            return None
        return payload if isinstance(payload, dict) else None

    # -- diffing ----------------------------------------------------------- #

    def diff(
        self,
        previous: Iterable[TaskHealth] | None,
        current: Iterable[TaskHealth] | None,
    ) -> list[HealthChange]:
        """Return only what changed between two checks (empty list when stable)."""
        before = {h.name: h for h in (previous or [])}
        after = {h.name: h for h in (current or [])}
        changes: list[HealthChange] = []

        for name, health in after.items():
            old = before.get(name)
            if old is None:
                changes.append(
                    HealthChange(
                        name=name,
                        kind=CHANGE_APPEARED,
                        level=LEVEL_ALERT if health.status in ALERT_STATUSES else LEVEL_INFO,
                        previous_status=None,
                        current_status=health.status,
                        detail=(
                            f"новая задача в реестре (статус {health.status})"
                            + (f": {health.detail}" if health.detail else "")
                        ),
                        health=health,
                    )
                )
                continue
            if old.status != health.status:
                changes.append(
                    HealthChange(
                        name=name,
                        kind=CHANGE_STATUS,
                        level=LEVEL_ALERT if health.status in ALERT_STATUSES else LEVEL_INFO,
                        previous_status=old.status,
                        current_status=health.status,
                        detail=health.detail or f"{old.status} → {health.status}",
                        health=health,
                    )
                )
                continue
            old_bucket = age_bucket(old)
            new_bucket = age_bucket(health)
            if old_bucket != new_bucket:
                changes.append(
                    HealthChange(
                        name=name,
                        kind=CHANGE_AGE,
                        level=LEVEL_INFO,
                        previous_status=old.status,
                        current_status=health.status,
                        detail=(
                            f"возраст state-файла: {_format_hours(old.age_hours)} → "
                            f"{_format_hours(health.age_hours)} "
                            f"({old_bucket} → {new_bucket}, порог "
                            f"{_format_hours(health.stale_after_hours)})"
                        ),
                        health=health,
                    )
                )

        for name, old in before.items():
            if name in after:
                continue
            changes.append(
                HealthChange(
                    name=name,
                    kind=CHANGE_DISAPPEARED,
                    level=LEVEL_INFO,
                    previous_status=old.status,
                    current_status=None,
                    detail="задача исчезла из реестра",
                    health=None,
                )
            )

        changes.sort(key=lambda c: (-c.severity, c.level != LEVEL_ALERT, c.name))
        return changes

    # -- validation against the real Scheduled Tasks ----------------------- #

    def validate_against_schtasks(
        self,
        *,
        run_command: Callable[..., subprocess.CompletedProcess[str]] = subprocess.run,
        system_tasks: Sequence[str] | None = None,
        registry: Registry | None = None,
    ) -> list[str]:
        """Compare the registry with ``schtasks /Query /FO CSV /V``."""
        reg = registry or self.load()
        if system_tasks is None:
            try:
                system_tasks = query_scheduled_tasks(run_command=run_command)
            except SchtasksError as exc:
                return [f"[error] {exc}"]

        messages: list[str] = []
        system_by_key: dict[str, str] = {}
        for raw in system_tasks:
            key = _normalize_task_name(raw)
            if key:
                system_by_key.setdefault(key, raw)
        registry_by_key = {_normalize_task_name(t.name): t.name for t in reg.tasks}

        for key, registry_name in sorted(registry_by_key.items()):
            system_name = system_by_key.get(key)
            if system_name is None:
                messages.append(
                    f"[missing] реестр ссылается на несуществующую задачу: '{registry_name}'"
                )
                continue
            leaf = system_name.strip().lstrip("\\")
            if leaf != registry_name.strip():
                messages.append(
                    f"[name] имя в системе отличается от реестра: "
                    f"система '{system_name}' vs реестр '{registry_name}'"
                )

        for key, system_name in sorted(system_by_key.items()):
            if key in registry_by_key:
                continue
            if self._is_ignored_task(system_name, reg.ignore_task_prefixes):
                continue
            messages.append(f"[extra] задача вне реестра: '{system_name}'")

        return messages

    @staticmethod
    def _is_ignored_task(system_name: str, prefixes: Sequence[str]) -> bool:
        name = (system_name or "").strip()
        if not name.startswith("\\"):
            name = "\\" + name
        folded = name.casefold()
        return any(prefix and folded.startswith(prefix.casefold()) for prefix in prefixes)

    # -- rendering --------------------------------------------------------- #

    def render_digest(
        self,
        changes: Sequence[HealthChange] | None,
        *,
        now: datetime | None = None,
        validation: Sequence[str] | None = None,
    ) -> str:
        """Render the single Telegram/console digest for a set of changes."""
        moment = (now or utc_now()).astimezone(timezone.utc)
        header = f"🗂 Реестр cron-задач — {moment.strftime('%Y-%m-%d %H:%M UTC')}"
        ordered = sorted(
            changes or [], key=lambda c: (-c.severity, c.level != LEVEL_ALERT, c.name)
        )
        if not ordered:
            lines = [header, "", "✅ Всё штатно: изменений с прошлого прогона нет."]
            lines.extend(_validation_block(validation))
            return "\n".join(lines)

        alerts = sum(1 for c in ordered if c.level == LEVEL_ALERT)
        lines = [
            header,
            "",
            f"Изменений: {len(ordered)} (alert: {alerts}, info: {len(ordered) - alerts})",
        ]
        for change in ordered:
            status = change.current_status or change.previous_status or STATUS_UNKNOWN_TASK
            icon = STATUS_ICONS.get(status, "⚪")
            title = f"{icon} {change.name} — {status}"
            if change.previous_status and change.current_status != change.previous_status:
                title += f" (было: {change.previous_status})"
            label = "что не так" if change.level == LEVEL_ALERT else "что изменилось"
            lines.extend(["", title, f"   {label}: {change.detail or '—'}"])
            health = change.health
            if health is not None:
                if health.state_path:
                    rel = self._relative(health.state_path)
                    lines.append(
                        f"   state: {rel} (обновлён {_format_hours(health.state_age_hours)} назад,"
                        f" порог {_format_hours(health.stale_after_hours)})"
                    )
                elif health.state_pattern:
                    lines.append(f"   state: нет файла по шаблону {health.state_pattern}")
                if health.last_success:
                    lines.append(f"   последний успех: {health.last_success}")
                if health.log_path:
                    lines.append(
                        f"   лог: {self._relative(health.log_path)} "
                        f"({_format_hours(health.log_age_hours)} назад)"
                    )
                if health.log_last_line:
                    lines.append(f"   последняя строка: {health.log_last_line[:160]}")
        lines.extend(_validation_block(validation))
        return "\n".join(lines)

    def _relative(self, path: str | None) -> str:
        if not path:
            return "-"
        try:
            return Path(path).relative_to(self.workspace_root).as_posix()
        except ValueError:
            return path

    def render_table(self, health: Sequence[TaskHealth]) -> str:
        """ASCII table of every registry entry (cp1251-safe console output)."""
        headers = ("TASK", "STATUS", "RC", "AGE", "PERIOD", "STALE>", "STATE", "DETAIL")
        rows: list[tuple[str, ...]] = []
        for item in health:
            code = item.scheduled_last_result
            if code is None:
                rc = "-"
            elif code == 0:
                rc = "0"
            else:
                rc = f"{code} (0x{code & 0xFFFFFFFF:08X})"
            rows.append(
                (
                    item.name,
                    item.status,
                    rc,
                    _format_hours(item.age_hours),
                    _format_hours(item.period_hours),
                    _format_hours(item.stale_after_hours),
                    self._relative(item.state_path) if item.state_path else "-",
                    item.detail or "-",
                )
            )
        widths = [len(h) for h in headers]
        for row in rows:
            for index, cell in enumerate(row):
                widths[index] = max(widths[index], len(cell))

        # The free-form detail column is last; do not pad it.
        def fmt(row: Sequence[str]) -> str:
            cells = [
                cell.ljust(widths[index]) if index < len(row) - 1 else cell
                for index, cell in enumerate(row)
            ]
            return "  ".join(cells).rstrip()

        lines = [fmt(headers), fmt(tuple("-" * widths[i] for i in range(len(headers))))]
        lines.extend(fmt(row) for row in rows)
        return "\n".join(lines)

    # -- previous-run snapshot --------------------------------------------- #

    def load_last_run(self) -> list[TaskHealth]:
        path = self.last_run_path
        if not path.is_file():
            return []
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError, UnicodeDecodeError):
            return []
        entries = payload.get("tasks") if isinstance(payload, dict) else payload
        if not isinstance(entries, list):
            return []
        result: list[TaskHealth] = []
        for entry in entries:
            if not isinstance(entry, dict):
                continue
            try:
                result.append(TaskHealth.from_dict(entry))
            except (TypeError, ValueError):
                continue
        return result

    def save_last_run(self, health: Sequence[TaskHealth], *, now: datetime | None = None) -> Path:
        path = self.last_run_path
        path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "version": 1,
            "checked_at": _iso(now or utc_now()),
            "tasks": [item.to_dict() for item in health],
        }
        tmp = path.with_suffix(path.suffix + ".tmp")
        tmp.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
        os.replace(tmp, path)
        return path

    # -- delivery ---------------------------------------------------------- #

    @staticmethod
    def send_digest(message: str, *, sender: Any | None = None) -> dict[str, Any]:
        """Deliver the digest through ``scripts/telegram_sender.py``."""
        text = message if len(message) <= TELEGRAM_TEXT_LIMIT else (
            message[: TELEGRAM_TEXT_LIMIT - 20] + "\n…(обрезано)"
        )
        if sender is None:
            try:
                from scripts.telegram_sender import (  # noqa: PLC0415 - optional dependency
                    TelegramConfig,
                    TelegramSender,
                    TelegramSenderError,
                )
            except ImportError as exc:
                return {"ok": False, "error": f"telegram_sender недоступен: {exc}"}
            try:
                sender = TelegramSender(TelegramConfig.from_env())
            except TelegramSenderError as exc:
                return {"ok": False, "error": str(exc)}
        try:
            payload = sender.send_message(text)
        except Exception as exc:  # noqa: BLE001 - never crash the cron on delivery
            return {"ok": False, "error": str(exc)}
        return {"ok": True, "response": payload}

    # -- CLI orchestration -------------------------------------------------- #

    def run(
        self,
        check: bool = True,
        diff: bool = True,
        validate: bool = True,
        send: bool = True,
        *,
        now: datetime | None = None,
        run_command: Callable[..., subprocess.CompletedProcess[str]] = subprocess.run,
        sender: Any | None = None,
        as_json: bool = False,
        printer: Callable[[str], None] = print,
    ) -> int:
        moment = (now or utc_now()).astimezone(timezone.utc)
        try:
            registry = self.load()
        except RegistryError as exc:
            printer(f"ОШИБКА: {exc}")
            return 2

        system_tasks: list[str] | None = None
        facts: dict[str, SchtaskFact] | None = None
        validation: list[str] = []
        # One schtasks call gives both the task list and the ground truth
        # (last run + exit code). The scheduler is the only source that knows
        # the real exit code of tasks that never write a state file.
        try:
            system_tasks, facts = query_schtasks(run_command=run_command)
        except SchtasksError as exc:
            if validate:
                validation.append(f"[error] {exc}")
        else:
            if validate:
                validation = self.validate_against_schtasks(
                    system_tasks=system_tasks, registry=registry
                )

        # The "does the task still exist" check only runs when validation was
        # requested; the exit codes are always useful ground truth.
        known_for_check = system_tasks if validate else None
        current = self.check(
            moment, system_tasks=known_for_check, registry=registry, facts=facts
        )
        changes = self.diff(self.load_last_run(), current) if diff else []
        digest = self.render_digest(
            changes, now=moment, validation=validation if validate else None
        )

        delivery: dict[str, Any] | None = None
        if send and changes:
            delivery = self.send_digest(digest, sender=sender)

        if diff:
            self.save_last_run(current, now=moment)

        exit_code = 1 if any(item.status in ALERT_STATUSES for item in current) else 0

        if as_json:
            printer(
                json.dumps(
                    {
                        "checked_at": _iso(moment),
                        "registry": str(self.registry_path),
                        "tasks": [item.to_dict() for item in current],
                        "changes": [change.to_dict() for change in changes],
                        "validation": validation,
                        "digest": digest,
                        "telegram": delivery,
                        "exit_code": exit_code,
                    },
                    indent=2,
                    ensure_ascii=False,
                )
            )
            return exit_code

        if check:
            printer(f"Реестр: {self.registry_path} (задач: {len(current)})")
            printer(self.render_table(current))
            printer("")
        if validate:
            printer("Сверка со schtasks:")
            if validation:
                for line in validation:
                    printer(f"  {line}")
            else:
                printer("  расхождений нет")
            printer("")
        if diff:
            printer(digest)
            if not changes:
                printer("")
                printer("Изменений нет — сообщение в Telegram не отправляется.")
            elif send:
                if delivery and delivery.get("ok"):
                    printer("")
                    printer("Telegram: отправлено.")
                else:
                    printer("")
                    printer(f"Telegram: НЕ отправлено ({(delivery or {}).get('error')})")
        return exit_code


def _validation_block(validation: Sequence[str] | None) -> list[str]:
    if not validation:
        return []
    lines = ["", "Проверка реестра против schtasks:"]
    lines.extend(f"• {line}" for line in validation[:20])
    if len(validation) > 20:
        lines.append(f"• …и ещё {len(validation) - 20} строк")
    return lines


def age_bucket(health: TaskHealth) -> str:
    """Coarse freshness bucket; used so the diff does not fire on every tick."""
    age = health.age_hours
    if age is None:
        return "unknown"
    period = float(health.period_hours or DEFAULT_PERIOD_HOURS)
    if period <= 0:
        return "unknown"
    ratio = age / period
    if ratio <= 1.0:
        return "fresh"
    threshold = float(health.stale_after_hours or period * DEFAULT_STALE_FACTOR)
    if age <= threshold:
        return "aging"
    return "late"


def sort_health(health: Iterable[TaskHealth]) -> list[TaskHealth]:
    return sorted(health, key=lambda item: (-item.severity, item.name.casefold()))


def _ensure_utf8_stdout() -> None:
    """Best-effort UTF-8 stdout so Cyrillic text survives a cp1251 console."""
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError):
            pass


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Единый реестр cron-задач с проверкой живости.",
    )
    parser.add_argument(
        "--registry",
        type=Path,
        default=None,
        help="Путь к cron_registry.json (по умолчанию data/cron_registry.json).",
    )
    parser.add_argument(
        "--root",
        type=Path,
        default=None,
        help="Корень workspace для резолва state_file/log_file (по умолчанию корень репозитория).",
    )
    parser.add_argument("--check", action="store_true", help="Показать таблицу здоровья задач.")
    parser.add_argument("--no-check", action="store_true", help="Не печатать таблицу.")
    parser.add_argument("--diff", action="store_true", help="Сравнить с прошлым прогоном.")
    parser.add_argument("--no-diff", action="store_true", help="Не сравнивать и не писать snapshot.")
    parser.add_argument("--validate", action="store_true", help="Сверить реестр со schtasks.")
    parser.add_argument("--no-validate", action="store_true", help="Не вызывать schtasks.")
    parser.add_argument("--send", action="store_true", help="Отправить дайджест в Telegram.")
    parser.add_argument("--no-send", action="store_true", help="Никогда не отправлять в Telegram.")
    parser.add_argument("--json", action="store_true", help="Печатать машинночитаемый JSON.")
    return parser


def resolve_stages(args: argparse.Namespace) -> dict[str, bool]:
    """``--check``/``--diff``/``--validate`` select stages; none given = all."""
    explicit = {
        "check": bool(args.check),
        "diff": bool(args.diff),
        "validate": bool(args.validate),
    }
    stages = explicit if any(explicit.values()) else {key: True for key in explicit}
    stages = dict(stages)
    if args.no_check:
        stages["check"] = False
    if args.no_diff:
        stages["diff"] = False
    if args.no_validate:
        stages["validate"] = False
    stages["send"] = bool(args.send) and not args.no_send
    return stages


def main(argv: Sequence[str] | None = None) -> int:
    _ensure_utf8_stdout()
    args = build_parser().parse_args(argv)
    stages = resolve_stages(args)
    registry = CronRegistry(registry_path=args.registry, workspace_root=args.root)
    return registry.run(
        check=stages["check"],
        diff=stages["diff"],
        validate=stages["validate"],
        send=stages["send"],
        as_json=args.json,
    )


if __name__ == "__main__":
    raise SystemExit(main())
