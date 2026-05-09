from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional


def _utc_iso_now() -> str:
    return datetime.now(timezone.utc).isoformat()


@dataclass(frozen=True)
class ErrorRecord:
    """A single captured error, failure, or correction from an agent interaction."""

    timestamp: str
    category: str
    description: str
    corrective_action: str

    def to_dict(self) -> Dict[str, Any]:
        return {
            "timestamp": self.timestamp,
            "category": self.category,
            "description": self.description,
            "corrective_action": self.corrective_action,
        }

    @staticmethod
    def from_dict(data: Dict[str, Any]) -> ErrorRecord:
        return ErrorRecord(
            timestamp=str(data["timestamp"]),
            category=str(data["category"]),
            description=str(data["description"]),
            corrective_action=str(data.get("corrective_action", "")),
        )


class LearningsDB:
    """Append-only store of structured learnings, backed by a JSON file on disk."""

    _FILE_VERSION = 1

    def __init__(self, path: Optional[Path] = None) -> None:
        self._path = path or (Path.home() / ".openclaw" / "error_learnings.json")
        self._records: List[ErrorRecord] = []
        self._load()

    @property
    def path(self) -> Path:
        return self._path

    def _load(self) -> None:
        if not self._path.exists():
            self._records = []
            return
        raw = self._path.read_text(encoding="utf-8")
        if not raw.strip():
            self._records = []
            return
        payload = json.loads(raw)
        items: List[Dict[str, Any]]
        if isinstance(payload, list):
            items = payload
        elif isinstance(payload, dict) and "records" in payload:
            items = list(payload["records"])
        else:
            raise ValueError(
                f"Unexpected JSON shape in {self._path}: expected a list or object with 'records'"
            )
        self._records = [ErrorRecord.from_dict(x) for x in items if isinstance(x, dict)]

    def _save(self) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        body = {
            "version": self._FILE_VERSION,
            "records": [r.to_dict() for r in self._records],
        }
        text = json.dumps(body, indent=2, ensure_ascii=False) + "\n"
        tmp = self._path.with_suffix(self._path.suffix + ".tmp")
        tmp.write_text(text, encoding="utf-8")
        tmp.replace(self._path)

    def log_error(
        self,
        category: str,
        description: str,
        corrective_action: str,
        *,
        timestamp: Optional[str] = None,
    ) -> ErrorRecord:
        """Record an error or correction and persist it to the JSON backing store."""
        record = ErrorRecord(
            timestamp=timestamp or _utc_iso_now(),
            category=category,
            description=description,
            corrective_action=corrective_action,
        )
        self._records.append(record)
        self._save()
        return record

    def get_recent_learnings(self, limit: int = 50) -> List[ErrorRecord]:
        """Return up to ``limit`` newest records (most recent first)."""
        if limit <= 0:
            return []
        return list(reversed(self._records[-limit:]))

    def export_learnings_markdown(self, limit: Optional[int] = None) -> str:
        """Serialize learnings as a Markdown document (newest entries first)."""
        if limit is None or limit <= 0:
            rows = list(reversed(self._records))
        else:
            rows = self.get_recent_learnings(limit)
        lines: List[str] = ["# Error learnings", ""]
        if not rows:
            lines.append("_No learnings recorded yet._")
            lines.append("")
            return "\n".join(lines)
        for i, r in enumerate(rows, start=1):
            lines.extend(
                [
                    f"## {i}. {r.category}",
                    "",
                    f"- **Timestamp:** `{r.timestamp}`",
                    f"- **Description:** {r.description}",
                    f"- **Corrective action:** {r.corrective_action}",
                    "",
                ]
            )
        return "\n".join(lines)
