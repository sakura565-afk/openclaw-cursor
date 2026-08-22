#!/usr/bin/env python3
"""Yandex Metrika anomaly detector — reads data/metrika_cache/ (no API calls)."""

from __future__ import annotations

import argparse
import json
import os
import statistics
import subprocess
import sys
import tempfile
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable, Iterable, Sequence

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

BASELINE_DAYS = 14
DROP_THRESHOLD_SITE = 35.0
DROP_THRESHOLD_LANDING = 50.0
RISE_THRESHOLD = 100.0
DEFAULT_CHAT_ID = "25979298"

DEFAULT_TELEGRAM_SCRIPT = Path(
    r"C:\Users\user\.openclaw\skills\telegram-media-send\scripts\telegram_media_send_v2.py"
)

JsonDict = dict[str, Any]


@dataclass(frozen=True)
class DayPoint:
    day: date
    visits: float
    visitors: float


@dataclass(frozen=True)
class CounterSeries:
    counter_id: int | str
    name: str
    kind: str
    points: tuple[DayPoint, ...]


@dataclass(frozen=True)
class Anomaly:
    day: date
    counter_id: int | str
    counter_name: str
    kind: str
    metric: str
    today: float
    avg_14d: float
    delta_pct: float
    z_score: float
    anomaly_type: str

    @property
    def alert_key(self) -> str:
        return f"{self.counter_id}:{self.metric}:{self.anomaly_type}"


def utc_today() -> date:
    return datetime.now(timezone.utc).date()


def cache_dir(root: Path | None = None) -> Path:
    return (root or ROOT) / "data" / "metrika_cache"


def anomalies_path(root: Path | None = None) -> Path:
    return (root or ROOT) / "data" / "metrika_anomalies.jsonl"


def _parse_day(value: Any) -> date | None:
    text = str(value or "").strip()
    if not text:
        return None
    try:
        return date.fromisoformat(text.split("T", 1)[0][:10])
    except ValueError:
        return None


def _float(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def infer_kind(path: Path, payload: JsonDict) -> str:
    explicit = str(payload.get("kind") or "").strip().lower()
    if explicit in {"landing", "site"}:
        return explicit
    parts = {part.lower() for part in path.parts}
    if "landings" in parts or "landing" in parts:
        return "landing"
    return "site"


def parse_cache_file(path: Path) -> CounterSeries | None:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    if not isinstance(payload, dict):
        return None

    rows = payload.get("daily") or payload.get("series") or payload.get("points")
    if not isinstance(rows, list):
        return None

    points: list[DayPoint] = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        day = _parse_day(row.get("date") or row.get("day"))
        if day is None:
            continue
        visitors = row.get("visitors")
        if visitors is None:
            visitors = row.get("users")
        points.append(
            DayPoint(
                day=day,
                visits=_float(row.get("visits")),
                visitors=_float(visitors),
            )
        )
    if not points:
        return None

    counter_id = payload.get("counter_id") or payload.get("id") or path.stem
    name = str(payload.get("name") or payload.get("label") or path.stem)
    kind = infer_kind(path, payload)
    points.sort(key=lambda p: p.day)
    return CounterSeries(
        counter_id=counter_id,
        name=name,
        kind=kind,
        points=tuple(points),
    )


def load_counter_caches(cache_root: Path) -> list[CounterSeries]:
    if not cache_root.is_dir():
        return []
    out: list[CounterSeries] = []
    for path in sorted(cache_root.rglob("*.json")):
        if path.name.startswith("."):
            continue
        series = parse_cache_file(path)
        if series is not None:
            out.append(series)
    return out


def value_by_day(points: Sequence[DayPoint], metric: str) -> dict[date, float]:
    out: dict[date, float] = {}
    for point in points:
        out[point.day] = point.visits if metric == "visits" else point.visitors
    return out


def baseline_samples(
    values: dict[date, float],
    today: date,
    *,
    days: int = BASELINE_DAYS,
) -> list[float]:
    samples: list[float] = []
    for offset in range(1, days + 1):
        day = today - timedelta(days=offset)
        if day in values:
            samples.append(values[day])
    return samples


def compute_baseline(samples: Sequence[float]) -> float:
    if not samples:
        return 0.0
    return statistics.mean(samples)


def pct_delta(today: float, baseline: float) -> float:
    if baseline == 0:
        return 0.0 if today == 0 else 100.0
    return (today - baseline) / baseline * 100.0


def compute_z_score(today: float, samples: Sequence[float]) -> float:
    if len(samples) < 2:
        return 0.0
    stdev = statistics.pstdev(samples)
    if stdev == 0:
        return 0.0
    return (today - statistics.mean(samples)) / stdev


def classify_anomaly(delta_pct: float, kind: str) -> str | None:
    drop_limit = DROP_THRESHOLD_LANDING if kind == "landing" else DROP_THRESHOLD_SITE
    if delta_pct <= -drop_limit:
        return "drop"
    if delta_pct >= RISE_THRESHOLD:
        return "rise"
    return None


def detect_counter_anomaly(
    series: CounterSeries,
    today: date,
    *,
    metric: str = "visits",
    days: int = BASELINE_DAYS,
) -> Anomaly | None:
    values = value_by_day(series.points, metric)
    if today not in values:
        return None
    samples = baseline_samples(values, today, days=days)
    if not samples:
        return None
    today_value = values[today]
    avg = compute_baseline(samples)
    delta = pct_delta(today_value, avg)
    anomaly_type = classify_anomaly(delta, series.kind)
    if anomaly_type is None:
        return None
    return Anomaly(
        day=today,
        counter_id=series.counter_id,
        counter_name=series.name,
        kind=series.kind,
        metric=metric,
        today=today_value,
        avg_14d=avg,
        delta_pct=delta,
        z_score=compute_z_score(today_value, samples),
        anomaly_type=anomaly_type,
    )


def load_alert_history(path: Path) -> list[JsonDict]:
    if not path.is_file():
        return []
    rows: list[JsonDict] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        text = line.strip()
        if not text:
            continue
        try:
            item = json.loads(text)
        except json.JSONDecodeError:
            continue
        if isinstance(item, dict):
            rows.append(item)
    return rows


def active_alert_keys(history: Iterable[JsonDict]) -> set[str]:
    active: dict[str, JsonDict] = {}
    for rec in sorted(history, key=lambda r: str(r.get("date") or "")):
        if rec.get("recovered"):
            key = str(rec.get("key") or "")
            if key:
                active.pop(key, None)
            continue
        key = str(rec.get("key") or "")
        if not key:
            continue
        if rec.get("alerted"):
            active[key] = rec
    return set(active.keys())


def anomaly_to_record(anomaly: Anomaly, *, alerted: bool, recovered: bool = False) -> JsonDict:
    return {
        "date": anomaly.day.isoformat(),
        "key": anomaly.alert_key,
        "counter_id": anomaly.counter_id,
        "counter_name": anomaly.counter_name,
        "kind": anomaly.kind,
        "metric": anomaly.metric,
        "anomaly_type": anomaly.anomaly_type,
        "today": anomaly.today,
        "avg_14d": anomaly.avg_14d,
        "delta_pct": round(anomaly.delta_pct, 2),
        "z_score": round(anomaly.z_score, 2),
        "alerted": alerted,
        "recovered": recovered,
    }


def append_jsonl(path: Path, records: Sequence[JsonDict]) -> None:
    if not records:
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        for rec in records:
            handle.write(json.dumps(rec, ensure_ascii=False) + "\n")


def format_anomaly_line(anomaly: Anomaly) -> str:
    return (
        f"Аномалия метрики {anomaly.day.isoformat()}: {anomaly.counter_name} "
        f"visits={anomaly.today:.0f} vs avg={anomaly.avg_14d:.0f} "
        f"({anomaly.delta_pct:+.1f}%, z={anomaly.z_score:.1f})"
    )


def format_alert(anomalies: Sequence[Anomaly]) -> str | None:
    if not anomalies:
        return None
    lines = [f"⚠️ Аномалии Яндекс Метрики ({len(anomalies)}):", ""]
    for item in anomalies:
        emoji = "📉" if item.anomaly_type == "drop" else "📈"
        lines.append(f"{emoji} {format_anomaly_line(item)}")
    return "\n".join(lines)


def format_human(anomalies: Sequence[Anomaly]) -> str:
    if not anomalies:
        return "No Metrika anomalies detected."
    lines = [f"Anomalies ({len(anomalies)}):"]
    for item in anomalies:
        lines.append(
            f"  - {item.counter_name} [{item.kind}] {item.anomaly_type}: "
            f"visits={item.today:.0f} avg={item.avg_14d:.0f} "
            f"delta={item.delta_pct:+.1f}% z={item.z_score:.1f}"
        )
    return "\n".join(lines)


def send_telegram_alert(
    message: str,
    *,
    caption: str,
    script_path: Path | None = None,
    run_command: Callable[..., subprocess.CompletedProcess[str]] = subprocess.run,
) -> JsonDict:
    script = script_path or Path(
        os.environ.get("METRIKA_ANOMALY_TELEGRAM_SCRIPT", str(DEFAULT_TELEGRAM_SCRIPT))
    )
    if not script.is_file():
        return {"ok": False, "error": f"telegram script not found: {script}"}
    with tempfile.NamedTemporaryFile(
        mode="w",
        encoding="utf-8",
        suffix=".txt",
        prefix="metrika_anomaly_",
        delete=False,
    ) as handle:
        handle.write(message)
        text_path = Path(handle.name)
    try:
        cmd = [sys.executable, str(script), str(text_path), "--caption", caption]
        env = os.environ.copy()
        env.setdefault("TELEGRAM_CHAT_ID", DEFAULT_CHAT_ID)
        env.setdefault("CHAT_ID", DEFAULT_CHAT_ID)
        completed = run_command(cmd, capture_output=True, text=True, check=False, env=env, timeout=60)
        if completed.returncode == 0:
            return {"ok": True, "command": cmd, "stdout": (completed.stdout or "").strip()[:500]}
        err = (completed.stderr or completed.stdout or "").strip() or f"exit {completed.returncode}"
        return {"ok": False, "error": err, "command": cmd}
    finally:
        try:
            text_path.unlink(missing_ok=True)
        except OSError:
            pass


def run_detector(
    *,
    root: Path | None = None,
    today: date | None = None,
    send_alert: bool = False,
    update_history: bool = False,
    metric: str = "visits",
    telegram_script: Path | None = None,
    run_command: Callable[..., subprocess.CompletedProcess[str]] = subprocess.run,
) -> JsonDict:
    base = root or ROOT
    day = today or utc_today()
    history_path = anomalies_path(base)
    history = load_alert_history(history_path)
    first_run = not history_path.is_file()
    active_keys = active_alert_keys(history)

    all_anomalies: list[Anomaly] = []
    for series in load_counter_caches(cache_dir(base)):
        item = detect_counter_anomaly(series, day, metric=metric)
        if item is not None:
            all_anomalies.append(item)

    to_alert: list[Anomaly] = []
    records: list[JsonDict] = []
    current_keys = {a.alert_key for a in all_anomalies}

    for anomaly in all_anomalies:
        should_alert = (not first_run) and anomaly.alert_key not in active_keys
        if send_alert and should_alert:
            to_alert.append(anomaly)
        if update_history:
            records.append(anomaly_to_record(anomaly, alerted=bool(send_alert and should_alert)))

    if update_history:
        for key in sorted(active_keys - current_keys):
            records.append(
                {
                    "date": day.isoformat(),
                    "key": key,
                    "recovered": True,
                    "alerted": False,
                }
            )
        append_jsonl(history_path, records)

    alert_text = format_alert(to_alert)
    caption = f"Metrika anomaly {day.isoformat()}"
    telegram_result: JsonDict | None = None
    if send_alert and alert_text:
        telegram_result = send_telegram_alert(
            alert_text,
            caption=caption,
            script_path=telegram_script,
            run_command=run_command,
        )
    elif send_alert:
        telegram_result = {"ok": True, "skipped": True, "reason": "no new anomalies"}

    return {
        "first_run": first_run,
        "day": day.isoformat(),
        "anomalies": all_anomalies,
        "new_alerts": to_alert,
        "history_path": str(history_path),
        "history_updated": update_history,
        "telegram": telegram_result,
    }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Detect Yandex Metrika traffic anomalies from data/metrika_cache/."
    )
    parser.add_argument("--send-alert", action="store_true", help="Send Telegram for new anomalies.")
    parser.add_argument(
        "--update-history",
        action="store_true",
        help="Append results to data/metrika_anomalies.jsonl.",
    )
    parser.add_argument("--print-json", action="store_true", help="Print anomalies as JSON.")
    parser.add_argument("--root", type=Path, default=None, help="Repository root.")
    parser.add_argument("--date", type=lambda s: date.fromisoformat(s), default=None, help="Analysis day.")
    parser.add_argument("--telegram-script", type=Path, default=None)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    report = run_detector(
        root=args.root,
        today=args.date,
        send_alert=args.send_alert,
        update_history=args.update_history,
        telegram_script=args.telegram_script,
    )

    if args.print_json:
        payload = [
            {
                "date": a.day.isoformat(),
                "counter_id": a.counter_id,
                "counter_name": a.counter_name,
                "kind": a.kind,
                "metric": a.metric,
                "anomaly_type": a.anomaly_type,
                "today": a.today,
                "avg_14d": a.avg_14d,
                "delta_pct": a.delta_pct,
                "z_score": a.z_score,
            }
            for a in report["anomalies"]
        ]
        print(json.dumps(payload, indent=2, ensure_ascii=False))
        return 0

    print(format_human(report["anomalies"]))
    if report.get("first_run"):
        print("(first run: seeded history without alerts)", file=sys.stderr)
    if report.get("history_updated"):
        print(f"Updated history: {report['history_path']}", file=sys.stderr)
    if args.send_alert and report.get("telegram"):
        tg = report["telegram"]
        if tg.get("skipped"):
            print("Telegram: skipped (no new anomalies)", file=sys.stderr)
        elif tg.get("ok"):
            print("Telegram: sent", file=sys.stderr)
        else:
            print(f"Telegram: FAILED — {tg.get('error')}", file=sys.stderr)
            return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
