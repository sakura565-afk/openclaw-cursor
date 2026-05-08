"""Persistent fingerprint store for deduplicated error observations."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from src.coordination.cross_bot_sync import atomic_write_json, read_json

STORE_VERSION = 1


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


@dataclass
class ErrorObservation:
    """One deduplicated error signal."""

    fingerprint: str
    category: str
    normalized_text: str
    count: int
    first_seen: str
    last_seen: str
    sources: dict[str, int] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "fingerprint": self.fingerprint,
            "category": self.category,
            "normalized_text": self.normalized_text,
            "count": self.count,
            "first_seen": self.first_seen,
            "last_seen": self.last_seen,
            "sources": dict(sorted(self.sources.items())),
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> ErrorObservation:
        return cls(
            fingerprint=str(data["fingerprint"]),
            category=str(data["category"]),
            normalized_text=str(data["normalized_text"]),
            count=int(data["count"]),
            first_seen=str(data["first_seen"]),
            last_seen=str(data["last_seen"]),
            sources=dict(data.get("sources") or {}),
        )


def compute_fingerprint(category: str, normalized_text: str) -> str:
    raw = f"{category}|{normalized_text}".encode("utf-8")
    return hashlib.sha256(raw).hexdigest()[:20]


class ErrorLearningStore:
    """JSON-backed store with atomic writes."""

    def __init__(self, path: Path) -> None:
        self.path = path

    def load(self) -> dict[str, Any]:
        data = read_json(self.path, default={})
        if not data:
            return {"version": STORE_VERSION, "updated_at": "", "observations": {}}
        if data.get("version") != STORE_VERSION:
            data = self._migrate(data)
        obs = data.get("observations")
        if not isinstance(obs, dict):
            data["observations"] = {}
        return data

    def _migrate(self, data: dict[str, Any]) -> dict[str, Any]:
        return {
            "version": STORE_VERSION,
            "updated_at": str(data.get("updated_at") or ""),
            "observations": data.get("observations")
            if isinstance(data.get("observations"), dict)
            else {},
        }

    def merge_observation(
        self,
        *,
        category: str,
        normalized_text: str,
        source: str | None,
    ) -> ErrorObservation:
        fp = compute_fingerprint(category, normalized_text)
        now = _utc_now().replace(microsecond=0).isoformat()
        data = self.load()
        observations: dict[str, Any] = data.setdefault("observations", {})
        raw = observations.get(fp)
        if isinstance(raw, dict):
            obs = ErrorObservation.from_dict(raw)
            obs.count += 1
            obs.last_seen = now
            if source:
                obs.sources[source] = obs.sources.get(source, 0) + 1
        else:
            obs = ErrorObservation(
                fingerprint=fp,
                category=category,
                normalized_text=normalized_text,
                count=1,
                first_seen=now,
                last_seen=now,
                sources={source: 1} if source else {},
            )
        observations[fp] = obs.to_dict()
        data["updated_at"] = now
        data["version"] = STORE_VERSION
        atomic_write_json(self.path, data)
        return obs

    def list_observations(self) -> list[ErrorObservation]:
        data = self.load()
        raw_obs = data.get("observations") or {}
        out: list[ErrorObservation] = []
        for fp, payload in raw_obs.items():
            if isinstance(payload, dict):
                payload.setdefault("fingerprint", fp)
                out.append(ErrorObservation.from_dict(payload))
        return sorted(out, key=lambda o: (-o.count, o.normalized_text.lower()))
