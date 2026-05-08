from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import tempfile
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable, Dict, Iterable, List, Optional, Sequence

try:
    import psutil  # type: ignore
except Exception:  # pragma: no cover - optional dependency
    psutil = None


class Colors:
    RESET = "\033[0m"
    BOLD = "\033[1m"
    RED = "\033[31m"
    GREEN = "\033[32m"
    YELLOW = "\033[33m"
    BLUE = "\033[34m"
    MAGENTA = "\033[35m"
    CYAN = "\033[36m"


def colorize(text: str, color: str, enabled: bool = True) -> str:
    if not enabled:
        return text
    return f"{color}{text}{Colors.RESET}"


def _now_utc() -> datetime:
    return datetime.now(timezone.utc)


@dataclass
class CheckResult:
    name: str
    status: str
    message: str
    details: Dict[str, Any] = field(default_factory=dict)

    def as_dict(self) -> Dict[str, Any]:
        return {
            "name": self.name,
            "status": self.status,
            "message": self.message,
            "details": self.details,
        }


@dataclass
class ImprovementAction:
    timestamp: str
    category: str
    action: str
    outcome: str
    details: Dict[str, Any] = field(default_factory=dict)

    def as_dict(self) -> Dict[str, Any]:
        return {
            "timestamp": self.timestamp,
            "category": self.category,
            "action": self.action,
            "outcome": self.outcome,
            "details": self.details,
        }


def _safe_int(value: Any) -> Optional[int]:
    try:
        if value is None:
            return None
        return int(float(value))
    except (TypeError, ValueError):
        return None


class AutoImprovementEngine:
    def __init__(
        self,
        root_dir: Path | str | None = None,
        log_dir: Path | str | None = None,
        *,
        command_runner: Optional[Callable[[Sequence[str]], subprocess.CompletedProcess[str]]] = None,
        ollama_restart_command: Optional[Sequence[str]] = None,
        color: Optional[bool] = None,
    ) -> None:
        self.root_dir = Path(root_dir or Path.cwd())
        self.log_dir = Path(log_dir or self.root_dir / "logs")
        self.log_dir.mkdir(parents=True, exist_ok=True)
        self.command_runner = command_runner or self._run_command
        self.ollama_restart_command = list(
            ollama_restart_command or ["systemctl", "--user", "restart", "ollama"]
        )
        self.color = color if color is not None else sys.stdout.isatty()

    def _run_command(self, command: Sequence[str]) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            list(command),
            check=False,
            capture_output=True,
            text=True,
        )

    def run_health_checks(self) -> List[CheckResult]:
        results = [
            self.check_gpu_health(),
            self.check_ollama_status(),
            self.check_disk_space(),
            self.check_memory_usage(),
        ]
        return results

    def check_gpu_health(self) -> CheckResult:
        if shutil.which("nvidia-smi") is None:
            return CheckResult(
                name="gpu",
                status="warning",
                message="nvidia-smi not available; GPU metrics unavailable",
                details={},
            )

        cmd = [
            "nvidia-smi",
            "--query-gpu=memory.total,memory.used,temperature.gpu",
            "--format=csv,noheader,nounits",
        ]
        result = self.command_runner(cmd)
        if result.returncode != 0:
            stderr = (result.stderr or "").strip() or "nvidia-smi failed"
            return CheckResult(
                name="gpu",
                status="warning",
                message=f"GPU query failed: {stderr}",
                details={"returncode": result.returncode},
            )

        line = (result.stdout or "").strip().splitlines()
        if not line:
            return CheckResult(
                name="gpu",
                status="warning",
                message="GPU query returned no data",
                details={},
            )

        parts = [part.strip() for part in line[0].split(",")]
        total_mib = _safe_int(parts[0] if len(parts) > 0 else None)
        used_mib = _safe_int(parts[1] if len(parts) > 1 else None)
        temp_c = _safe_int(parts[2] if len(parts) > 2 else None)
        percent_used = None
        if total_mib and used_mib is not None and total_mib > 0:
            percent_used = round((used_mib / total_mib) * 100, 1)

        status = "ok"
        alerts: List[str] = []
        if percent_used is not None and percent_used >= 95:
            status = "critical"
            alerts.append(f"VRAM usage high at {percent_used}%")
        elif percent_used is not None and percent_used >= 85:
            status = "warning"
            alerts.append(f"VRAM usage elevated at {percent_used}%")

        if temp_c is not None and temp_c >= 90:
            status = "critical"
            alerts.append(f"GPU temperature critical at {temp_c}C")
        elif temp_c is not None and temp_c >= 80 and status != "critical":
            status = "warning"
            alerts.append(f"GPU temperature elevated at {temp_c}C")

        if not alerts:
            alerts.append("GPU metrics healthy")

        return CheckResult(
            name="gpu",
            status=status,
            message="; ".join(alerts),
            details={
                "memory_total_mib": total_mib,
                "memory_used_mib": used_mib,
                "memory_percent_used": percent_used,
                "temperature_c": temp_c,
            },
        )

    def check_ollama_status(self) -> CheckResult:
        command = ["ollama", "list"]
        if shutil.which("ollama") is None:
            return CheckResult(
                name="ollama",
                status="warning",
                message="Ollama CLI not available",
                details={},
            )

        result = self.command_runner(command)
        if result.returncode != 0:
            stderr = (result.stderr or "").strip() or "ollama command failed"
            return CheckResult(
                name="ollama",
                status="critical",
                message=f"Ollama unavailable: {stderr}",
                details={"returncode": result.returncode},
            )

        return CheckResult(
            name="ollama",
            status="ok",
            message="Ollama responding normally",
            details={"summary": (result.stdout or "").strip()[:200]},
        )

    def check_disk_space(self) -> CheckResult:
        usage = shutil.disk_usage(self.root_dir)
        free_percent = round((usage.free / usage.total) * 100, 1) if usage.total else 0.0
        status = "ok"
        if free_percent <= 5:
            status = "critical"
            message = f"Disk space critically low: {free_percent}% free"
        elif free_percent <= 15:
            status = "warning"
            message = f"Disk space getting low: {free_percent}% free"
        else:
            message = f"Disk space healthy: {free_percent}% free"

        return CheckResult(
            name="disk",
            status=status,
            message=message,
            details={
                "total_bytes": usage.total,
                "used_bytes": usage.used,
                "free_bytes": usage.free,
                "free_percent": free_percent,
            },
        )

    def check_memory_usage(self) -> CheckResult:
        if psutil is not None:
            vm = psutil.virtual_memory()
            used_percent = float(vm.percent)
            available_bytes = int(vm.available)
            total_bytes = int(vm.total)
        else:
            page_size = os.sysconf("SC_PAGE_SIZE")
            total_pages = os.sysconf("SC_PHYS_PAGES")
            avail_pages = os.sysconf("SC_AVPHYS_PAGES")
            total_bytes = int(page_size * total_pages)
            available_bytes = int(page_size * avail_pages)
            used_percent = round(
                ((total_bytes - available_bytes) / total_bytes) * 100,
                1,
            ) if total_bytes else 0.0

        status = "ok"
        if used_percent >= 95:
            status = "critical"
            message = f"Memory usage critically high: {used_percent}% used"
        elif used_percent >= 85:
            status = "warning"
            message = f"Memory usage elevated: {used_percent}% used"
        else:
            message = f"Memory usage healthy: {used_percent}% used"

        return CheckResult(
            name="memory",
            status=status,
            message=message,
            details={
                "used_percent": used_percent,
                "available_bytes": available_bytes,
                "total_bytes": total_bytes,
            },
        )

    def log_warning(self, message: str, *, details: Optional[Dict[str, Any]] = None) -> ImprovementAction:
        action = ImprovementAction(
            timestamp=_now_utc().isoformat(),
            category="warning",
            action=message,
            outcome="logged",
            details=details or {},
        )
        self._append_log_entry(action)
        return action

    def clear_temp_files(self) -> ImprovementAction:
        temp_root = Path(tempfile.gettempdir())
        removed_items = 0
        failed_items = 0
        managed_prefixes = ("openclaw_", "ollama_", "auto_engine_")
        for child in temp_root.iterdir():
            name = child.name
            if not name.startswith(managed_prefixes):
                continue
            try:
                if child.is_dir():
                    shutil.rmtree(child)
                else:
                    child.unlink()
                removed_items += 1
            except OSError:
                failed_items += 1

        outcome = "cleared" if removed_items else "no_action"
        action = ImprovementAction(
            timestamp=_now_utc().isoformat(),
            category="cleanup",
            action="clear_temp_files",
            outcome=outcome,
            details={
                "temp_dir": str(temp_root),
                "removed_items": removed_items,
                "failed_items": failed_items,
            },
        )
        self._append_log_entry(action)
        return action

    def restart_ollama(self) -> ImprovementAction:
        result = self.command_runner(self.ollama_restart_command)
        success = result.returncode == 0
        action = ImprovementAction(
            timestamp=_now_utc().isoformat(),
            category="service",
            action="restart_ollama",
            outcome="restarted" if success else "failed",
            details={
                "command": list(self.ollama_restart_command),
                "returncode": result.returncode,
                "stdout": (result.stdout or "").strip(),
                "stderr": (result.stderr or "").strip(),
            },
        )
        self._append_log_entry(action)
        return action

    def auto_fix(self) -> List[ImprovementAction]:
        actions: List[ImprovementAction] = []
        checks = self.run_health_checks()

        ollama_result = next((item for item in checks if item.name == "ollama"), None)
        if ollama_result and ollama_result.status != "ok":
            actions.append(self.restart_ollama())

        disk_result = next((item for item in checks if item.name == "disk"), None)
        if disk_result and disk_result.status in {"warning", "critical"}:
            actions.append(self.clear_temp_files())

        for result in checks:
            if result.status in {"warning", "critical"}:
                actions.append(
                    self.log_warning(
                        f"{result.name} health issue detected",
                        details=result.as_dict(),
                    )
                )

        if not actions:
            actions.append(
                ImprovementAction(
                    timestamp=_now_utc().isoformat(),
                    category="info",
                    action="auto_fix",
                    outcome="no_action_needed",
                    details={},
                )
            )
            self._append_log_entry(actions[-1])

        return actions

    def status_report(self) -> Dict[str, Any]:
        checks = self.run_health_checks()
        overall = "ok"
        if any(item.status == "critical" for item in checks):
            overall = "critical"
        elif any(item.status == "warning" for item in checks):
            overall = "warning"

        return {
            "timestamp": _now_utc().isoformat(),
            "overall_status": overall,
            "checks": [item.as_dict() for item in checks],
        }

    def _log_path_for_day(self, day: Optional[date] = None) -> Path:
        target_day = day or _now_utc().date()
        return self.log_dir / f"auto_improvements_{target_day.strftime('%Y%m%d')}.json"

    def _append_log_entry(self, action: ImprovementAction) -> None:
        path = self._log_path_for_day()
        entries = []
        if path.exists():
            try:
                entries = json.loads(path.read_text(encoding="utf-8"))
                if not isinstance(entries, list):
                    entries = []
            except json.JSONDecodeError:
                entries = []
        entries.append(action.as_dict())
        path.write_text(json.dumps(entries, indent=2, sort_keys=True), encoding="utf-8")

    def iter_log_entries(self, *, since_days: Optional[int] = None) -> Iterable[Dict[str, Any]]:
        cutoff: Optional[date] = None
        if since_days is not None:
            cutoff = (_now_utc() - timedelta(days=since_days)).date()

        for path in sorted(self.log_dir.glob("auto_improvements_*.json")):
            stem = path.stem.rsplit("_", 1)[-1]
            try:
                file_day = datetime.strptime(stem, "%Y%m%d").date()
            except ValueError:
                continue
            if cutoff and file_day < cutoff:
                continue
            try:
                payload = json.loads(path.read_text(encoding="utf-8"))
            except json.JSONDecodeError:
                continue
            if isinstance(payload, list):
                for item in payload:
                    if isinstance(item, dict):
                        yield item

    def generate_weekly_digest(self) -> str:
        entries = list(self.iter_log_entries(since_days=7))
        generated_at = _now_utc().strftime("%Y-%m-%d %H:%M:%S %Z")
        lines = [
            "# Weekly Auto-Improvement Digest",
            "",
            f"Generated: {generated_at}",
            "",
        ]

        if not entries:
            lines.append("No improvements were recorded in the last 7 days.")
            return "\n".join(lines) + "\n"

        counts: Dict[str, int] = {}
        for entry in entries:
            category = str(entry.get("category", "unknown"))
            counts[category] = counts.get(category, 0) + 1

        lines.append("## Summary")
        lines.append("")
        for category in sorted(counts):
            lines.append(f"- {category}: {counts[category]}")
        lines.append("")
        lines.append("## Improvements")
        lines.append("")

        for entry in entries:
            timestamp = str(entry.get("timestamp", "unknown-time"))
            category = str(entry.get("category", "unknown"))
            action = str(entry.get("action", "unknown-action"))
            outcome = str(entry.get("outcome", "unknown"))
            lines.append(f"- **{timestamp}** `{category}` - {action} ({outcome})")

        digest = "\n".join(lines) + "\n"
        digest_path = self.log_dir / "weekly_auto_improvement_digest.md"
        digest_path.write_text(digest, encoding="utf-8")
        return digest

    def run_self_reflection(
        self,
        *,
        lookback_days: int = 7,
        max_bytes_per_file: int = 2_000_000,
        transcript_dirs: Optional[Sequence[Path | str]] = None,
    ) -> Dict[str, Any]:
        """
        Run the transcript / ``.learnings`` self-reflection pass using this engine's paths.

        Writes ``self_reflection_YYYYMMDD.{md,json}`` under ``log_dir``. Returns the report
        as a plain dictionary (JSON-serializable).
        """
        from src.self_improvement.auto_reflection import ReflectionConfig, run_reflection

        td: Optional[Sequence[Path]] = None
        if transcript_dirs is not None:
            td = [Path(p).expanduser() for p in transcript_dirs]

        config = ReflectionConfig.from_env_and_args(
            root_dir=self.root_dir,
            log_dir=self.log_dir,
            transcript_dirs=td,
            lookback_days=lookback_days,
            max_bytes_per_file=max_bytes_per_file,
        )
        report = run_reflection(config)
        return report.to_dict()

    def format_check_result(self, result: CheckResult) -> str:
        color = Colors.GREEN
        if result.status == "warning":
            color = Colors.YELLOW
        elif result.status == "critical":
            color = Colors.RED
        status = colorize(result.status.upper(), color, self.color)
        return f"[{status}] {result.name}: {result.message}"

    def format_action(self, action: ImprovementAction) -> str:
        color = Colors.GREEN if action.outcome not in {"failed"} else Colors.RED
        outcome = colorize(action.outcome.upper(), color, self.color)
        return f"[{outcome}] {action.category}: {action.action}"


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="OpenClaw autonomous self-improvement engine")
    parser.add_argument(
        "command",
        choices=["status", "check", "fix", "digest", "reflect"],
        help="Action to run",
    )
    parser.add_argument(
        "--root-dir",
        default=".",
        help="Repository root directory used for disk checks and log output",
    )
    parser.add_argument(
        "--no-color",
        action="store_true",
        help="Disable ANSI color output",
    )
    return parser


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    engine = AutoImprovementEngine(
        root_dir=Path(args.root_dir),
        color=not args.no_color,
    )

    if args.command == "status":
        report = engine.status_report()
        overall = report["overall_status"].upper()
        overall_color = Colors.GREEN
        if report["overall_status"] == "warning":
            overall_color = Colors.YELLOW
        elif report["overall_status"] == "critical":
            overall_color = Colors.RED
        print(colorize(f"Overall status: {overall}", overall_color, engine.color))
        for check in report["checks"]:
            print(engine.format_check_result(CheckResult(**check)))
        return 0

    if args.command == "check":
        checks = engine.run_health_checks()
        exit_code = 0
        for check in checks:
            print(engine.format_check_result(check))
            if check.status == "critical":
                exit_code = 2
            elif check.status == "warning" and exit_code == 0:
                exit_code = 1
        return exit_code

    if args.command == "fix":
        actions = engine.auto_fix()
        for action in actions:
            print(engine.format_action(action))
        return 0

    if args.command == "digest":
        digest = engine.generate_weekly_digest()
        print(colorize("Weekly digest generated", Colors.CYAN, engine.color))
        print(digest)
        return 0

    report = engine.run_self_reflection()
    print(colorize("Self-reflection report generated", Colors.CYAN, engine.color))
    day = _now_utc().strftime("%Y%m%d")
    print(
        colorize(
            f"Artifacts: {engine.log_dir / f'self_reflection_{day}.md'} "
            f"and {engine.log_dir / f'self_reflection_{day}.json'}",
            Colors.GREEN,
            engine.color,
        )
    )
    overall = report.get("aggregate_failure_hits", 0) <= report.get("aggregate_success_hits", 0)
    print(
        colorize(
            f"Signals: success_hits={report.get('aggregate_success_hits')} "
            f"failure_hits={report.get('aggregate_failure_hits')} "
            f"files_scanned={report.get('files_scanned')}",
            Colors.GREEN if overall else Colors.YELLOW,
            engine.color,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
