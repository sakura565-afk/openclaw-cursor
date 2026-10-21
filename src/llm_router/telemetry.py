"""Append-only телеметрия роутера в JSONL + агрегаты и ротация.

Одна строка — одно решение оркестратора. Файл растёт до
``telemetry_max_bytes``, после чего уезжает в
``data/llm_router_telemetry/<ts>.jsonl.gz``.
"""

from __future__ import annotations

import gzip
import json
import shutil
import threading
import time
from collections import Counter, defaultdict
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, Iterator, List, Optional

#: Набор полей, который обязан присутствовать в каждой записи.
RECORD_FIELDS = (
    "ts",
    "task",
    "decision",
    "model",
    "latency_ms",
    "cache_hit",
    "tokens",
    "confidence",
    "outcome",
    "error",
)

VALID_OUTCOMES = frozenset({"ok", "error", "escalated"})


def utc_now_iso() -> str:
    """ISO 8601 в UTC с секундной точностью."""

    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


@dataclass
class TelemetryRecord:
    """Одна строка телеметрии."""

    task: str
    decision: Dict[str, Any] = field(default_factory=dict)
    model: Optional[str] = None
    latency_ms: float = 0.0
    cache_hit: bool = False
    tokens: Dict[str, int] = field(default_factory=dict)
    confidence: Optional[float] = None
    outcome: str = "ok"
    error: Optional[str] = None
    ts: str = field(default_factory=utc_now_iso)
    extra: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        payload = asdict(self)
        extra = payload.pop("extra", {}) or {}
        payload["latency_ms"] = round(float(payload.get("latency_ms") or 0.0), 3)
        payload["cache_hit"] = bool(payload.get("cache_hit"))
        if payload.get("outcome") not in VALID_OUTCOMES:
            payload["outcome"] = "error" if payload.get("error") else "ok"
        ordered = {name: payload.get(name) for name in RECORD_FIELDS}
        for key, value in extra.items():
            if key not in ordered:
                ordered[key] = value
        return ordered


class Telemetry:
    """Писатель и агрегатор телеметрии."""

    def __init__(
        self,
        path: Path,
        *,
        max_bytes: int = 5 * 1024 * 1024,
        archive_dir: Optional[Path] = None,
        enabled: bool = True,
    ) -> None:
        self.path = Path(path)
        self.max_bytes = int(max_bytes)
        self.archive_dir = Path(archive_dir) if archive_dir else self.path.with_suffix("")
        self.enabled = bool(enabled)
        self._lock = threading.Lock()

    # --- запись ---

    def record(self, record: TelemetryRecord | Dict[str, Any], **kwargs: Any) -> Dict[str, Any]:
        """Дописать строку. Возвращает то, что реально ушло в файл."""

        if isinstance(record, TelemetryRecord):
            payload = record.to_dict()
        else:
            payload = TelemetryRecord(**{**record, **kwargs}).to_dict()
        if not self.enabled:
            return payload
        line = json.dumps(payload, ensure_ascii=False)
        with self._lock:
            self.rotate_if_needed()
            self.path.parent.mkdir(parents=True, exist_ok=True)
            with self.path.open("a", encoding="utf-8") as stream:
                stream.write(line + "\n")
        return payload

    def log(self, task: str, **kwargs: Any) -> Dict[str, Any]:
        """Сахар над :meth:`record` для вызова по именованным полям."""

        return self.record(TelemetryRecord(task=task, **kwargs))

    def rotate_if_needed(self) -> Optional[Path]:
        """Если файл перерос лимит — сжать его в архив и начать новый."""

        if self.max_bytes <= 0 or not self.path.is_file():
            return None
        if self.path.stat().st_size < self.max_bytes:
            return None
        self.archive_dir.mkdir(parents=True, exist_ok=True)
        stamp = time.strftime("%Y%m%dT%H%M%S", time.gmtime())
        target = self.archive_dir / f"{stamp}.jsonl.gz"
        counter = 1
        while target.exists():
            target = self.archive_dir / f"{stamp}-{counter}.jsonl.gz"
            counter += 1
        with self.path.open("rb") as source, gzip.open(target, "wb") as archive:
            shutil.copyfileobj(source, archive)
        self.path.unlink(missing_ok=True)
        return target

    # --- чтение ---

    def iter_records(self, *, include_archive: bool = False) -> Iterator[Dict[str, Any]]:
        """Перебрать записи; битые строки пропускаются."""

        sources: List[Path] = []
        if include_archive and self.archive_dir.is_dir():
            sources.extend(sorted(self.archive_dir.glob("*.jsonl.gz")))
        if self.path.is_file():
            sources.append(self.path)
        for source in sources:
            opener = gzip.open if source.suffix == ".gz" else open
            try:
                with opener(source, "rt", encoding="utf-8") as stream:  # type: ignore[operator]
                    for line in stream:
                        line = line.strip()
                        if not line:
                            continue
                        try:
                            payload = json.loads(line)
                        except json.JSONDecodeError:
                            continue
                        if isinstance(payload, dict):
                            yield payload
            except OSError:
                continue

    def records(self, *, include_archive: bool = False) -> List[Dict[str, Any]]:
        return list(self.iter_records(include_archive=include_archive))

    # --- агрегаты ---

    def summary(self, *, include_archive: bool = False) -> Dict[str, Any]:
        """Hit rate, средняя латентность по tier, доля эскалаций, топ ошибок."""

        return summarize(self.iter_records(include_archive=include_archive))


def summarize(records: Iterable[Dict[str, Any]]) -> Dict[str, Any]:
    """Посчитать агрегаты по готовому итератору записей."""

    total = 0
    cache_hits = 0
    escalations = 0
    errors = 0
    latency_by_tier: Dict[str, List[float]] = defaultdict(list)
    tier_counts: Counter[str] = Counter()
    model_counts: Counter[str] = Counter()
    error_counts: Counter[str] = Counter()
    confidences: List[float] = []

    for payload in records:
        total += 1
        decision = payload.get("decision") or {}
        tier = str(decision.get("tier") or "unknown") if isinstance(decision, dict) else "unknown"
        tier_counts[tier] += 1
        if payload.get("cache_hit"):
            cache_hits += 1
        outcome = payload.get("outcome")
        if outcome == "escalated":
            escalations += 1
        if outcome == "error" or payload.get("error"):
            errors += 1
            error_counts[str(payload.get("error") or "unknown")[:200]] += 1
        latency = payload.get("latency_ms")
        if isinstance(latency, (int, float)):
            latency_by_tier[tier].append(float(latency))
        confidence = payload.get("confidence")
        if isinstance(confidence, (int, float)):
            confidences.append(float(confidence))
        model = payload.get("model")
        if model:
            model_counts[str(model)] += 1

    def _mean(values: List[float]) -> float:
        return round(sum(values) / len(values), 3) if values else 0.0

    return {
        "total": total,
        "cache_hits": cache_hits,
        "hit_rate": round(cache_hits / total, 4) if total else 0.0,
        "escalations": escalations,
        "escalation_rate": round(escalations / total, 4) if total else 0.0,
        "errors": errors,
        "error_rate": round(errors / total, 4) if total else 0.0,
        "avg_latency_ms_by_tier": {
            tier: _mean(values) for tier, values in sorted(latency_by_tier.items())
        },
        "avg_latency_ms": _mean([v for values in latency_by_tier.values() for v in values]),
        "avg_confidence": _mean(confidences),
        "tier_counts": dict(tier_counts),
        "model_counts": dict(model_counts),
        "top_errors": [
            {"error": message, "count": count} for message, count in error_counts.most_common(5)
        ],
    }
