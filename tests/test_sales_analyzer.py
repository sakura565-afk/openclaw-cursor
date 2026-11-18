from __future__ import annotations

import json
from pathlib import Path

import pytest

from furniture_sales_database.sales_tools.analyzer import analyze, period_days, render_json

FIXTURE_DB = (
    Path(__file__).resolve().parents[1]
    / "furniture_sales_database/sales_tools/fixtures/sales_sample.db"
)


@pytest.fixture(scope="module")
def sample_db() -> str:
    assert FIXTURE_DB.is_file(), "fixture sales_sample.db missing"
    return FIXTURE_DB.as_posix()


def test_analyze_groups_sum_matches_slice(sample_db: str) -> None:
    report = analyze(sample_db, "2026-11")
    groups_sum = sum(g["amount"] for g in report["groups"])
    assert abs(groups_sum - report["slice"]["amount"]) < 0.01


def test_coverage_ok_and_no_unassigned(sample_db: str) -> None:
    report = analyze(sample_db, "2026-11")
    assert report["coverage"]["sum_ok"] is True
    assert report["coverage"]["unassigned"] == []


def test_dynamics_per_day_uses_period_split(sample_db: str) -> None:
    report = analyze(sample_db, "2026-11", compare_month="2026-10")
    assert report["dynamics"], "expected dynamics vs 2026-10"
    days = period_days(report["slice"]["period"])
    assert days == 18
    by_group = {d["group"]: d for d in report["dynamics"]}
    amt = next(g["amount"] for g in report["groups"] if g["name"] == "Стулья")
    assert abs(by_group["Стулья"]["per_day"] - amt / days) < 0.01
    assert by_group["Стулья"]["delta_pct"] is not None


def test_render_json_valid_and_sum_ok(sample_db: str) -> None:
    report = analyze(sample_db, "2026-11")
    raw = render_json(report)
    payload = json.loads(raw)
    assert payload["coverage"]["sum_ok"] is True
    groups_sum = sum(g["amount"] for g in payload["groups"])
    assert abs(groups_sum - payload["slice"]["amount"]) < 0.01