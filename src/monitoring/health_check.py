"""
Cron-style health checks for OpenClaw and Gateway availability.
"""
from __future__ import annotations

import argparse
import json
import subprocess
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional
from urllib import error, request

DEFAULT_SESSION_THRESHOLD = 80.0
DEFAULT_GATEWAY_URL = "http://localhost:8080/health"
DEFAULT_INTERVAL_SECONDS = 60


@dataclass
class HealthReport:
    timestamp: str
    openclaw_ok: bool
    gateway_ok: bool
    max_session_percent: float
    alerts: List[str]
    details: Dict[str, Any]

    def to_dict(self) -> Dict[str, Any]:
        return {
            "timestamp": self.timestamp,
            "openclaw_ok": self.openclaw_ok,
            "gateway_ok": self.gateway_ok,
            "max_session_percent": self.max_session_percent,
            "alerts": self.alerts,
            "details": self.details,
        }


def _utc_timestamp() -> str:
    return datetime.now(timezone.utc).isoformat()


def _extract_session_percentages(status_payload: Dict[str, Any]) -> List[float]:
    sessions = status_payload.get("sessions", {})
    recent = sessions.get("recent", [])
    percentages: List[float] = []
    for session in recent:
        value = session.get("percentUsed")
        if isinstance(value, (int, float)):
            percentages.append(float(value))
    return percentages


def check_openclaw_status(timeout_seconds: int = 30) -> Dict[str, Any]:
    try:
        completed = subprocess.run(
            ["npx", "openclaw", "status", "--json"],
            check=False,
            text=True,
            capture_output=True,
            timeout=timeout_seconds,
        )
    except (subprocess.SubprocessError, OSError) as exc:
        return {"ok": False, "error": str(exc), "sessions_percent": []}

    if completed.returncode != 0:
        return {
            "ok": False,
            "error": completed.stderr.strip() or "openclaw status failed",
            "sessions_percent": [],
        }

    try:
        payload = json.loads(completed.stdout)
    except json.JSONDecodeError as exc:
        return {"ok": False, "error": f"invalid json: {exc}", "sessions_percent": []}

    return {
        "ok": True,
        "payload": payload,
        "sessions_percent": _extract_session_percentages(payload),
    }


def check_gateway_health(url: str = DEFAULT_GATEWAY_URL, timeout_seconds: int = 10) -> Dict[str, Any]:
    req = request.Request(url=url, method="GET")
    try:
        with request.urlopen(req, timeout=timeout_seconds) as response:
            status_code = response.getcode()
            body = response.read().decode("utf-8", errors="replace")
            return {
                "ok": 200 <= status_code < 300,
                "status_code": status_code,
                "body": body[:500],
            }
    except error.URLError as exc:
        return {"ok": False, "error": str(exc)}


def run_health_check(
    session_threshold: float = DEFAULT_SESSION_THRESHOLD,
    gateway_url: str = DEFAULT_GATEWAY_URL,
) -> HealthReport:
    openclaw = check_openclaw_status()
    gateway = check_gateway_health(gateway_url)

    session_values = openclaw.get("sessions_percent", [])
    max_session = max(session_values) if session_values else 0.0
    alerts: List[str] = []

    if not gateway.get("ok", False):
        alerts.append("Gateway health check failed")
    if max_session > session_threshold:
        alerts.append(
            f"Session usage high: {max_session:.2f}% (threshold {session_threshold:.2f}%)"
        )

    report = HealthReport(
        timestamp=_utc_timestamp(),
        openclaw_ok=bool(openclaw.get("ok", False)),
        gateway_ok=bool(gateway.get("ok", False)),
        max_session_percent=float(max_session),
        alerts=alerts,
        details={"openclaw": openclaw, "gateway": gateway},
    )
    return report


def _print_report(report: HealthReport) -> None:
    rendered = json.dumps(report.to_dict(), indent=2)
    print(rendered)
    if report.alerts:
        for alert_message in report.alerts:
            print(f"ALERT: {alert_message}")


def run_daemon(interval_seconds: int, session_threshold: float, gateway_url: str) -> None:
    try:
        import schedule
    except ImportError as exc:
        raise RuntimeError(
            "The 'schedule' library is required. Install it with: pip install schedule"
        ) from exc

    def _scheduled_job() -> None:
        report = run_health_check(
            session_threshold=session_threshold,
            gateway_url=gateway_url,
        )
        _print_report(report)

    _scheduled_job()
    schedule.every(interval_seconds).seconds.do(_scheduled_job)
    while True:
        schedule.run_pending()
        time.sleep(1)


def main() -> None:
    parser = argparse.ArgumentParser(description="OpenClaw/Gateway health monitor")
    subparsers = parser.add_subparsers(dest="command", required=True)

    run_parser = subparsers.add_parser("run", help="Run health checks")
    run_parser.add_argument(
        "--daemon",
        action="store_true",
        help="Run continuously using scheduled intervals",
    )
    run_parser.add_argument(
        "--interval-seconds",
        type=int,
        default=DEFAULT_INTERVAL_SECONDS,
        help=f"Seconds between checks in daemon mode (default: {DEFAULT_INTERVAL_SECONDS})",
    )
    run_parser.add_argument(
        "--session-threshold",
        type=float,
        default=DEFAULT_SESSION_THRESHOLD,
        help=f"Alert threshold percentage for sessions (default: {DEFAULT_SESSION_THRESHOLD})",
    )
    run_parser.add_argument(
        "--gateway-url",
        type=str,
        default=DEFAULT_GATEWAY_URL,
        help=f"Gateway health endpoint (default: {DEFAULT_GATEWAY_URL})",
    )

    args = parser.parse_args()
    if args.command == "run":
        if args.daemon:
            run_daemon(
                interval_seconds=args.interval_seconds,
                session_threshold=args.session_threshold,
                gateway_url=args.gateway_url,
            )
        else:
            report = run_health_check(
                session_threshold=args.session_threshold,
                gateway_url=args.gateway_url,
            )
            _print_report(report)


if __name__ == "__main__":
    main()
