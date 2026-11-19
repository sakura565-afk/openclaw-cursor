#!/usr/bin/env python3
"""Daily Yandex Metrika time series, dynamics, and resilient aggregation.

Builds per-day stats with one API request per calendar day (no
``dimensions=ym:s:date`` — that dimension only returns the last day on live API).
Requests run sequentially with an optional pause to stay under per-account parallel
quotas.

Counter metadata (site vs landing) is loaded from ``docs/metrika_counters.json``.
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass
from datetime import date, timedelta
from pathlib import Path
from typing import Any, Iterable, Mapping, Protocol

REPO_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_COUNTERS_PATH = REPO_ROOT / "docs" / "metrika_counters.json"

# Matches scripts/metrika_costs_margin.py pacing (parallel quota per OAuth UID).
REQUEST_PACE_SECONDS = 1.50

METRIC_KEYS: tuple[str, ...] = (
    "visits",
    "pageviews",
    "users",
    "bounce_rate",
    "avg_visit_duration_seconds",
)

ND_PLACEHOLDER = "н/д"
NEW_PERIOD_LABEL = "новый"


@dataclass(frozen=True)
class DayAggregates:
    visits: float
    pageviews: float
    users: float
    bounce_rate: float
    avg_visit_duration_seconds: float


@dataclass(frozen=True)
class CounterRecord:
    counter_id: int
    label: str
    kind: str


@dataclass(frozen=True)
class GroupTotals:
    """Summed traffic metrics with visit-weighted bounce and duration."""

    visits: float
    pageviews: float
    users: float
    bounce_rate: float
    avg_visit_duration_seconds: float
    counters_included: int


class DailyStatClient(Protocol):
    """Minimal client used by :func:`build_daily_series`."""

    def fetch_day(self, counter_id: int, day: date) -> dict[str, Any] | None:
        """Return a Metrika ``/stat/v1/data`` JSON object, or ``None`` on failure."""


def iter_days(date1: date, date2: date) -> list[date]:
    if date2 < date1:
        date1, date2 = date2, date1
    days: list[date] = []
    cursor = date1
    while cursor <= date2:
        days.append(cursor)
        cursor += timedelta(days=1)
    return days


def load_counter_registry(path: Path = DEFAULT_COUNTERS_PATH) -> list[CounterRecord]:
    with path.open(encoding="utf-8") as handle:
        payload = json.load(handle)
    rows = payload.get("counters", [])
    out: list[CounterRecord] = []
    for row in rows:
        # Поддерживаются обе схемы реестра: name/type (каноничная,
        # metrika_tools.counters) и label/kind (legacy).
        label = str(row.get("name") or row.get("label") or "")
        kind = str(row.get("type") or row.get("kind") or "site")
        if kind == "main":
            kind = "site"
        out.append(
            CounterRecord(
                counter_id=int(row["id"]),
                label=label,
                kind=kind,
            )
        )
    return out


def partition_by_kind(registry: Iterable[CounterRecord]) -> tuple[list[CounterRecord], list[CounterRecord]]:
    sites: list[CounterRecord] = []
    landings: list[CounterRecord] = []
    for row in registry:
        if row.kind == "landing":
            landings.append(row)
        else:
            sites.append(row)
    return sites, landings


def safe_float(value: Any) -> float:
    if value is None:
        return 0.0
    if isinstance(value, bool):
        return float(int(value))
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str):
        text = value.strip().replace(",", ".")
        if not text:
            return 0.0
        try:
            return float(text)
        except ValueError:
            return 0.0
    return 0.0


def extract_totals_row(payload: Mapping[str, Any]) -> list[Any] | None:
    """Normalize ``totals`` whether flat or nested per metric/group."""
    totals = payload.get("totals")
    if totals is None:
        return None
    if not isinstance(totals, list) or len(totals) == 0:
        return None
    first = totals[0]
    if isinstance(first, list):
        if not first:
            return None
        row = first[0] if isinstance(first[0], list) else first
        return list(row) if isinstance(row, list) else [row]
    return list(totals)


def parse_day_aggregates(payload: Mapping[str, Any]) -> DayAggregates | None:
    """Parse one day of Metrika totals; broken fields degrade to zero, missing totals → gap."""
    row = extract_totals_row(payload)
    if row is None:
        return None
    padded = list(row) + [None] * max(0, 5 - len(row))
    return DayAggregates(
        visits=safe_float(padded[0]),
        pageviews=safe_float(padded[1]),
        users=safe_float(padded[2]),
        bounce_rate=safe_float(padded[3]),
        avg_visit_duration_seconds=safe_float(padded[4]),
    )


def build_daily_series(
    client: DailyStatClient,
    counter_id: int,
    date1: date,
    date2: date,
    *,
    pace_seconds: float = REQUEST_PACE_SECONDS,
) -> dict[date, DayAggregates | None]:
    """One request per day, sequentially; gaps instead of raised errors."""
    series: dict[date, DayAggregates | None] = {}
    days = iter_days(date1, date2)
    for index, day in enumerate(days):
        payload: dict[str, Any] | None
        try:
            payload = client.fetch_day(counter_id, day)
        except Exception:
            payload = None
        if payload is None:
            series[day] = None
        else:
            series[day] = parse_day_aggregates(payload)
        if pace_seconds > 0 and index < len(days) - 1:
            time.sleep(pace_seconds)
    return series


def is_counter_unavailable(series: Mapping[date, DayAggregates | None]) -> bool:
    """Counter is unavailable only when every day in the series is a gap."""
    if not series:
        return True
    return all(value is None for value in series.values())


def sum_metric(series: Mapping[date, DayAggregates | None], field: str) -> float:
    total = 0.0
    for value in series.values():
        if value is None:
            continue
        total += getattr(value, field)
    return total


def weighted_average(pairs: Iterable[tuple[float, float]]) -> float:
    """Visit-weighted mean of (metric_value, weight)."""
    numerator = 0.0
    denominator = 0.0
    for metric_value, weight in pairs:
        if weight <= 0:
            continue
        numerator += metric_value * weight
        denominator += weight
    if denominator <= 0:
        return 0.0
    return numerator / denominator


def simple_average(values: Iterable[float]) -> float:
    items = list(values)
    if not items:
        return 0.0
    return sum(items) / len(items)


def aggregates_from_series(series: Mapping[date, DayAggregates | None]) -> GroupTotals:
    visit_weights: list[tuple[float, float]] = []
    bounce_weights: list[tuple[float, float]] = []
    duration_weights: list[tuple[float, float]] = []
    counters_included = 0
    for value in series.values():
        if value is None:
            continue
        counters_included += 1
        visit_weights.append((value.visits, value.visits))
        bounce_weights.append((value.bounce_rate, value.visits))
        duration_weights.append((value.avg_visit_duration_seconds, value.visits))

    # When ``series`` maps one counter × many days, we sum days; when many counters ×
    # one day each, caller passes per-counter single-day slices — same aggregation.
    visits = sum_metric(series, "visits")
    pageviews = sum_metric(series, "pageviews")
    users = sum_metric(series, "users")
    bounce = weighted_average(bounce_weights)
    duration = weighted_average(duration_weights)
    return GroupTotals(
        visits=visits,
        pageviews=pageviews,
        users=users,
        bounce_rate=bounce,
        avg_visit_duration_seconds=duration,
        counters_included=counters_included,
    )


def format_change_pct(current: float, previous: float) -> str:
    if previous == 0:
        if current == 0:
            return "+0.0%"
        return NEW_PERIOD_LABEL
    delta_pct = (current - previous) / previous * 100.0
    sign = "+" if delta_pct >= 0 else ""
    return f"{sign}{delta_pct:.1f}%"


def previous_period(date1: date, date2: date) -> tuple[date, date]:
    days = iter_days(date1, date2)
    length = len(days)
    if length == 0:
        return date1, date1
    prev_end = date1 - timedelta(days=1)
    prev_start = prev_end - timedelta(days=length - 1)
    return prev_start, prev_end


def dynamics_for_metric(
    current_series: Mapping[date, DayAggregates | None],
    previous_series: Mapping[date, DayAggregates | None],
    field: str,
) -> str:
    current = sum_metric(current_series, field)
    previous = sum_metric(previous_series, field)
    return format_change_pct(current, previous)


@dataclass(frozen=True)
class CounterDynamics:
    counter_id: int
    label: str
    kind: str
    unavailable: bool
    visits_change: str
    pageviews_change: str
    users_change: str


def build_counter_dynamics(
    registry: Iterable[CounterRecord],
    current_series_by_counter: Mapping[int, Mapping[date, DayAggregates | None]],
    previous_series_by_counter: Mapping[int, Mapping[date, DayAggregates | None]],
) -> list[CounterDynamics]:
    out: list[CounterDynamics] = []
    for record in registry:
        current = current_series_by_counter.get(record.counter_id, {})
        previous = previous_series_by_counter.get(record.counter_id, {})
        unavailable = is_counter_unavailable(current)
        out.append(
            CounterDynamics(
                counter_id=record.counter_id,
                label=record.label,
                kind=record.kind,
                unavailable=unavailable,
                visits_change=ND_PLACEHOLDER if unavailable else dynamics_for_metric(current, previous, "visits"),
                pageviews_change=ND_PLACEHOLDER if unavailable else dynamics_for_metric(current, previous, "pageviews"),
                users_change=ND_PLACEHOLDER if unavailable else dynamics_for_metric(current, previous, "users"),
            )
        )
    return out


def _aggregate_group(
    counter_rows: list[CounterRecord],
    series_by_counter: Mapping[int, Mapping[date, DayAggregates | None]],
) -> GroupTotals:
    visits = pageviews = users = 0.0
    bounce_pairs: list[tuple[float, float]] = []
    duration_pairs: list[tuple[float, float]] = []
    included = 0
    for record in counter_rows:
        series = series_by_counter.get(record.counter_id, {})
        if is_counter_unavailable(series):
            continue
        included += 1
        counter_visits = sum_metric(series, "visits")
        visits += counter_visits
        pageviews += sum_metric(series, "pageviews")
        users += sum_metric(series, "users")
        if counter_visits > 0:
            inner = aggregates_from_series(series)
            bounce_pairs.append((inner.bounce_rate, counter_visits))
            duration_pairs.append((inner.avg_visit_duration_seconds, counter_visits))
    return GroupTotals(
        visits=visits,
        pageviews=pageviews,
        users=users,
        bounce_rate=weighted_average(bounce_pairs),
        avg_visit_duration_seconds=weighted_average(duration_pairs),
        counters_included=included,
    )


def summarize_groups(
    registry: Iterable[CounterRecord],
    series_by_counter: Mapping[int, Mapping[date, DayAggregates | None]],
) -> tuple[GroupTotals, GroupTotals]:
    """Return (sites_totals, landings_totals) for the supplied per-counter series."""
    sites, landings = partition_by_kind(registry)
    return _aggregate_group(sites, series_by_counter), _aggregate_group(landings, series_by_counter)


def format_day_value(value: DayAggregates | None, field: str) -> str:
    if value is None:
        return ND_PLACEHOLDER
    raw = getattr(value, field)
    if field in ("bounce_rate",):
        return f"{raw:.1f}%"
    if field in ("avg_visit_duration_seconds",):
        return f"{raw:.0f}s"
    return f"{raw:.0f}"