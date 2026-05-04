import importlib.util
import json
import subprocess
import sys
from datetime import date
from pathlib import Path


MODULE_PATH = Path(__file__).resolve().parents[1] / "scripts" / "memory_analytics.py"
SPEC = importlib.util.spec_from_file_location("memory_analytics", MODULE_PATH)
memory_analytics = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
SPEC.loader.exec_module(memory_analytics)


SAMPLE_MEMORY = """# OpenClaw Memory

## Active Work
- 2026-04-30: Investigated renderer memory spike in [Rendering](#rendering)
- 2026-03-01: Follow up on ancient cache cleanup
- 2026-04-29: Shared triage note with [Ghost](#missing-anchor)

## Rendering
- 2026-04-30: Investigated renderer memory spike in rendering pipeline
- 2026-04-28: Investigated renderer memory spikes in rendering pipeline

## Notes
Apr 10, 2026: Consider cache budget adjustments.
Continuation details mention 2026-04-11 for comparison.
"""


def test_parse_and_detect_issues():
    parsed = memory_analytics.parse_memory_content(SAMPLE_MEMORY)

    assert [section["title"] for section in parsed["sections"][1:]] == [
        "OpenClaw Memory",
        "Active Work",
        "Rendering",
        "Notes",
    ]
    assert len(parsed["entries"]) == 6
    assert parsed["entries"][0]["dates"] == ["2026-04-30"]
    assert parsed["entries"][-1]["dates"] == ["2026-04-10", "2026-04-11"]

    missing = memory_analytics.detect_missing_cross_references(parsed)
    assert missing == [
        {"anchor": "missing-anchor", "line": 5, "text": "[Ghost](#missing-anchor)"}
    ]

    duplicates = memory_analytics.detect_duplicate_entries(parsed["entries"], threshold=0.8)
    assert len(duplicates) == 1
    assert duplicates[0]["entry_a"]["line"] == 4
    assert duplicates[0]["entry_b"]["line"] == 8


def test_report_generation_and_stale_entries(tmp_path):
    input_path = tmp_path / "MEMORY.md"
    input_path.write_text(SAMPLE_MEMORY, encoding="utf-8")

    report = memory_analytics.analyze_memory_file(
        input_path=input_path,
        threshold_days=30,
        today=date(2026, 5, 4),
    )

    stats = report["statistics"]
    assert stats["total_entries"] == 6
    assert stats["sections_count"] == 4
    assert stats["age_distribution"] == {
        "0-7 days": 4,
        "8-30 days": 1,
        "31-90 days": 1,
        "91+ days": 0,
        "undated": 0,
    }

    stale_entries = report["stale_entries"]
    assert [entry["line"] for entry in stale_entries] == [4]
    assert stale_entries[0]["age_days"] == 64

    markdown = memory_analytics.generate_markdown_report(report)
    assert "# Memory Health Report" in markdown
    assert "## Missing Cross-References" in markdown
    assert "unresolved anchor `#missing-anchor`" in markdown

    logs_dir = tmp_path / "logs"
    json_path = memory_analytics.write_json_report(report, logs_dir, today=date(2026, 5, 4))
    payload = json.loads(json_path.read_text(encoding="utf-8"))
    assert payload["statistics"]["sections_count"] == 4


def test_cli_writes_markdown_and_json_reports(tmp_path):
    input_path = tmp_path / "MEMORY.md"
    output_path = tmp_path / "report.md"
    input_path.write_text(SAMPLE_MEMORY, encoding="utf-8")

    result = subprocess.run(
        [
            sys.executable,
            str(MODULE_PATH),
            "--input",
            str(input_path),
            "--output",
            str(output_path),
            "--days",
            "30",
        ],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    assert "\033[" in result.stdout
    assert "Memory Health Analytics" in result.stdout
    assert output_path.exists()

    json_path = tmp_path / "logs" / "memory_analytics_20260504.json"
    if not json_path.exists():
        matching_reports = sorted((tmp_path / "logs").glob("memory_analytics_*.json"))
        assert matching_reports, "expected a JSON analytics report"
        json_path = matching_reports[0]

    payload = json.loads(json_path.read_text(encoding="utf-8"))
    assert payload["missing_cross_references"][0]["anchor"] == "missing-anchor"
