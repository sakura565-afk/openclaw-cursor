from __future__ import annotations

import json
import logging
import os
import re
import time
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

_SAFE_SEGMENT = re.compile(r"[^a-zA-Z0-9._-]+")


def _safe_segment(value: str) -> str:
    return _SAFE_SEGMENT.sub("_", value)[:120]


class DiskCache:
    """JSON file cache with TTL and atomic writes."""

    def __init__(self, root: Path, *, ttl_seconds: float = 4 * 3600) -> None:
        self.root = root
        self.ttl_seconds = ttl_seconds

    def _path_for_key(self, counter_id: int, day: str, metrics_key: str) -> Path:
        return self.root / str(counter_id) / day / f"{_safe_segment(metrics_key)}.json"

    def get(self, counter_id: int, day: str, metrics_key: str) -> dict[str, Any] | None:
        path = self._path_for_key(counter_id, day, metrics_key)
        if not path.is_file():
            return None
        try:
            raw = path.read_text(encoding="utf-8")
            envelope = json.loads(raw)
        except (OSError, json.JSONDecodeError) as exc:
            logger.warning("Ignoring corrupt Metrika cache file %s: %s", path, exc)
            return None
        if not isinstance(envelope, dict):
            logger.warning("Ignoring invalid Metrika cache envelope in %s", path)
            return None
        fetched_at = envelope.get("fetched_at")
        payload = envelope.get("payload")
        if not isinstance(fetched_at, (int, float)) or payload is None:
            logger.warning("Ignoring malformed Metrika cache entry in %s", path)
            return None
        if time.time() - float(fetched_at) > self.ttl_seconds:
            return None
        if not isinstance(payload, dict):
            return None
        return payload

    def set(self, counter_id: int, day: str, metrics_key: str, payload: dict[str, Any]) -> None:
        path = self._path_for_key(counter_id, day, metrics_key)
        path.parent.mkdir(parents=True, exist_ok=True)
        envelope = {"fetched_at": time.time(), "payload": payload}
        tmp_path = path.with_suffix(path.suffix + ".tmp")
        try:
            tmp_path.write_text(json.dumps(envelope, ensure_ascii=False), encoding="utf-8")
            os.replace(tmp_path, path)
        except OSError:
            if tmp_path.exists():
                try:
                    tmp_path.unlink()
                except OSError:
                    pass
            raise