from __future__ import annotations

import argparse
import json
import traceback
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional


class ErrorLogger:
    """Persist runtime errors and surface repeat patterns with hints."""

    DEFAULT_HINTS: Dict[str, str] = {
        "ValueError": "Validate and sanitize input data before processing.",
        "KeyError": "Use dict.get() with defaults and check required keys upfront.",
        "TypeError": "Add explicit type checks at API boundaries.",
        "IndexError": "Guard list/array access with bounds checks.",
        "FileNotFoundError": "Verify the file path exists before opening files.",
        "ConnectionError": "Add retries with backoff and a connectivity health check.",
        "TimeoutError": "Increase timeout carefully and add retry-on-timeout logic.",
    }

    def __init__(self, base_dir: Path | str = "logs/errors") -> None:
        self.base_dir = Path(base_dir)
        self.base_dir.mkdir(parents=True, exist_ok=True)
        self.patterns_path = self.base_dir / "patterns.json"
        self._ensure_patterns_file()

    def _ensure_patterns_file(self) -> None:
        if not self.patterns_path.exists():
            self.patterns_path.write_text(
                json.dumps(self.DEFAULT_HINTS, indent=2, sort_keys=True),
                encoding="utf-8",
            )

    @staticmethod
    def _utc_now() -> str:
        return datetime.now(timezone.utc).isoformat()

    @staticmethod
    def _sanitize_name(value: str) -> str:
        return "".join(ch if ch.isalnum() or ch in {"-", "_"} else "_" for ch in value)

    def _load_pattern_hints(self) -> Dict[str, str]:
        self._ensure_patterns_file()
        try:
            payload = json.loads(self.patterns_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            payload = {}
        if not isinstance(payload, dict):
            payload = {}
        return {str(key): str(value) for key, value in payload.items()}

    def _write_pattern_hints(self, hints: Dict[str, str]) -> None:
        self.patterns_path.write_text(
            json.dumps(hints, indent=2, sort_keys=True),
            encoding="utf-8",
        )

    def _signature_for_error(self, payload: Dict[str, Any]) -> str:
        error_type = str(payload.get("type", "UnknownError"))
        message = str(payload.get("message", "")).strip().splitlines()[0] if payload.get("message") else ""
        if message:
            return f"{error_type}:{message}"
        return error_type

    def log(
        self,
        error: BaseException,
        *,
        stack_trace: Optional[str] = None,
        context: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """Store one error as JSON and return the persisted payload."""
        if stack_trace is None:
            stack_trace = "".join(traceback.format_exception(type(error), error, error.__traceback__))

        payload: Dict[str, Any] = {
            "timestamp": self._utc_now(),
            "type": type(error).__name__,
            "message": str(error),
            "stack_trace": stack_trace,
            "context": context or {},
        }

        safe_type = self._sanitize_name(payload["type"]) or "UnknownError"
        safe_ts = self._sanitize_name(payload["timestamp"])
        log_path = self.base_dir / f"{safe_ts}_{safe_type}.json"
        log_path.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")

        signature = self._signature_for_error(payload)
        hints = self._load_pattern_hints()
        if signature not in hints:
            hints[signature] = hints.get(
                payload["type"],
                "Add targeted validation, defensive checks, and regression tests for this error path.",
            )
            self._write_pattern_hints(hints)

        return payload

    def get_recent(self, limit: int = 10) -> List[Dict[str, Any]]:
        """Return most recent error payloads (newest first)."""
        entries: List[tuple[float, Dict[str, Any]]] = []
        for path in self.base_dir.glob("*.json"):
            if path.name == "patterns.json":
                continue
            try:
                payload = json.loads(path.read_text(encoding="utf-8"))
            except json.JSONDecodeError:
                continue
            if not isinstance(payload, dict):
                continue
            ts_value = str(payload.get("timestamp", ""))
            try:
                ts = datetime.fromisoformat(ts_value).timestamp()
            except ValueError:
                ts = path.stat().st_mtime
            entries.append((ts, payload))

        entries.sort(key=lambda item: item[0], reverse=True)
        return [item[1] for item in entries[: max(0, limit)]]

    def get_patterns(self) -> List[Dict[str, Any]]:
        """Analyze recurring signatures and attach prevention hints."""
        hints = self._load_pattern_hints()
        recent = self.get_recent(limit=10_000)
        counts: Dict[str, int] = {}

        for payload in recent:
            signature = self._signature_for_error(payload)
            counts[signature] = counts.get(signature, 0) + 1

        patterns: List[Dict[str, Any]] = []
        for signature, count in sorted(counts.items(), key=lambda kv: kv[1], reverse=True):
            error_type = signature.split(":", 1)[0]
            hint = hints.get(signature) or hints.get(error_type) or (
                "Review this path and add preventive guardrails and tests."
            )
            patterns.append(
                {
                    "signature": signature,
                    "count": count,
                    "prevention_hint": hint,
                }
            )
        return patterns


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Error learning and pattern analysis logger")
    parser.add_argument(
        "--base-dir",
        default="logs/errors",
        help="Directory used for error logs and patterns.json",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    log_parser = subparsers.add_parser("log", help="Log a synthetic error for quick testing")
    log_parser.add_argument("--type", default="ValueError", help="Synthetic error class name")
    log_parser.add_argument("--message", required=True, help="Synthetic error message")

    recent_parser = subparsers.add_parser("recent", help="Print recent errors as JSON")
    recent_parser.add_argument("--limit", type=int, default=10, help="Number of errors to show")

    subparsers.add_parser("patterns", help="Print learned error patterns as JSON")
    return parser


def main() -> int:
    parser = _build_parser()
    args = parser.parse_args()
    logger = ErrorLogger(base_dir=args.base_dir)

    if args.command == "log":
        error_cls = type(args.type, (Exception,), {})
        payload = logger.log(error_cls(args.message))
        print(json.dumps(payload, indent=2, sort_keys=True))
        return 0

    if args.command == "recent":
        print(json.dumps(logger.get_recent(limit=args.limit), indent=2, sort_keys=True))
        return 0

    print(json.dumps(logger.get_patterns(), indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
