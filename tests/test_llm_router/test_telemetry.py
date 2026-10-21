"""Телеметрия: полнота записи, агрегаты и ротация в gzip."""

from __future__ import annotations

import gzip
import json
from pathlib import Path

from src.llm_router.telemetry import RECORD_FIELDS, Telemetry, TelemetryRecord


def test_record_contains_all_required_fields(tmp_path: Path) -> None:
    telemetry = Telemetry(tmp_path / "telemetry.jsonl")

    written = telemetry.record(
        TelemetryRecord(
            task="general",
            decision={"tier": "fast", "strategy": "rule"},
            model="MiniMax-M3.1-Flash",
            latency_ms=123.456,
            cache_hit=False,
            tokens={"input_tokens": 10, "output_tokens": 20},
            confidence=0.82,
            outcome="ok",
        )
    )

    assert set(RECORD_FIELDS).issubset(written)
    line = json.loads((tmp_path / "telemetry.jsonl").read_text(encoding="utf-8").strip())
    assert line["task"] == "general"
    assert line["decision"] == {"tier": "fast", "strategy": "rule"}
    assert line["model"] == "MiniMax-M3.1-Flash"
    assert line["latency_ms"] == 123.456
    assert line["cache_hit"] is False
    assert line["tokens"]["output_tokens"] == 20
    assert line["confidence"] == 0.82
    assert line["outcome"] == "ok"
    assert line["error"] is None
    assert line["ts"].endswith("+00:00")


def test_summary_computes_hit_rate_and_tier_latency(tmp_path: Path) -> None:
    telemetry = Telemetry(tmp_path / "telemetry.jsonl")

    telemetry.log(
        "a", decision={"tier": "fast"}, latency_ms=100.0, cache_hit=True, confidence=0.9
    )
    telemetry.log("b", decision={"tier": "fast"}, latency_ms=300.0, confidence=0.7)
    telemetry.log(
        "c", decision={"tier": "deep"}, latency_ms=900.0, outcome="escalated", confidence=0.8
    )
    telemetry.log("d", decision={"tier": "deep"}, latency_ms=50.0, outcome="error", error="HTTP 429")

    summary = telemetry.summary()

    assert summary["total"] == 4
    assert summary["cache_hits"] == 1
    assert summary["hit_rate"] == 0.25
    assert summary["escalation_rate"] == 0.25
    assert summary["avg_latency_ms_by_tier"]["fast"] == 200.0
    assert summary["avg_latency_ms_by_tier"]["deep"] == 475.0
    assert summary["errors"] == 1
    assert summary["top_errors"][0] == {"error": "HTTP 429", "count": 1}


def test_summary_on_empty_file(tmp_path: Path) -> None:
    summary = Telemetry(tmp_path / "missing.jsonl").summary()

    assert summary["total"] == 0
    assert summary["hit_rate"] == 0.0
    assert summary["top_errors"] == []


def test_rotation_archives_to_gzip(tmp_path: Path) -> None:
    path = tmp_path / "telemetry.jsonl"
    archive_dir = tmp_path / "archive"
    telemetry = Telemetry(path, max_bytes=200, archive_dir=archive_dir)

    for index in range(6):
        telemetry.log(f"task-{index}", decision={"tier": "fast"}, latency_ms=float(index))

    archives = list(archive_dir.glob("*.jsonl.gz"))
    assert archives, "ротация не создала архив"

    with gzip.open(archives[0], "rt", encoding="utf-8") as stream:
        archived = [json.loads(line) for line in stream if line.strip()]
    assert archived
    # Часть истории уехала в архив, но суммарно записи все на месте.
    live = telemetry.records()
    assert len(live) < 6
    assert len(telemetry.records(include_archive=True)) == 6
    assert path.stat().st_size < 200 + len(json.dumps(live[0], ensure_ascii=False))


def test_corrupt_lines_are_skipped(tmp_path: Path) -> None:
    path = tmp_path / "telemetry.jsonl"
    telemetry = Telemetry(path)
    telemetry.log("ok", decision={"tier": "fast"}, latency_ms=1.0)
    with path.open("a", encoding="utf-8") as stream:
        stream.write("{битая строка\n")

    assert len(telemetry.records()) == 1
    assert telemetry.summary()["total"] == 1


def test_disabled_telemetry_writes_nothing(tmp_path: Path) -> None:
    path = tmp_path / "telemetry.jsonl"
    telemetry = Telemetry(path, enabled=False)

    payload = telemetry.log("task", decision={"tier": "fast"})

    assert payload["task"] == "task"
    assert not path.exists()
