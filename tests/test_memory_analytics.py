import importlib.util
import json
import subprocess
import sys
import tempfile
import unittest
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


class MemoryAnalyticsTests(unittest.TestCase):
    def test_parse_and_detect_issues(self) -> None:
        parsed = memory_analytics.parse_memory_content(SAMPLE_MEMORY)

        self.assertEqual(
            [section["title"] for section in parsed["sections"][1:]],
            ["OpenClaw Memory", "Active Work", "Rendering", "Notes"],
        )
        self.assertEqual(len(parsed["entries"]), 6)
        self.assertEqual(parsed["entries"][0]["dates"], ["2026-04-30"])
        self.assertEqual(parsed["entries"][-1]["dates"], ["2026-04-10", "2026-04-11"])

        missing = memory_analytics.detect_missing_cross_references(parsed)
        self.assertEqual(
            missing,
            [{"anchor": "missing-anchor", "line": 6, "text": "[Ghost](#missing-anchor)"}],
        )

        duplicates = memory_analytics.detect_duplicate_entries(parsed["entries"], threshold=0.8)
        duplicate_lines = {
            tuple(sorted((duplicate["entry_a"]["line"], duplicate["entry_b"]["line"])))
            for duplicate in duplicates
        }
        self.assertIn((4, 9), duplicate_lines)
        self.assertIn((9, 10), duplicate_lines)

    def test_report_generation_and_stale_entries(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            tmp_path = Path(tmp_dir)
            input_path = tmp_path / "MEMORY.md"
            input_path.write_text(SAMPLE_MEMORY, encoding="utf-8")

            report = memory_analytics.analyze_memory_file(
                input_path=input_path,
                threshold_days=30,
                today=date(2026, 5, 4),
            )

            stats = report["statistics"]
            self.assertEqual(stats["total_entries"], 6)
            self.assertEqual(stats["sections_count"], 4)
            self.assertEqual(
                stats["age_distribution"],
                {
                    "0-7 days": 4,
                    "8-30 days": 1,
                    "31-90 days": 1,
                    "91+ days": 0,
                    "undated": 0,
                },
            )

            stale_entries = report["stale_entries"]
            self.assertEqual([entry["line"] for entry in stale_entries], [5])
            self.assertEqual(stale_entries[0]["age_days"], 64)

            markdown = memory_analytics.generate_markdown_report(report)
            self.assertIn("# Memory Health Report", markdown)
            self.assertIn("## Missing Cross-References", markdown)
            self.assertIn("unresolved anchor `#missing-anchor`", markdown)

            logs_dir = tmp_path / "logs"
            json_path = memory_analytics.write_json_report(report, logs_dir, today=date(2026, 5, 4))
            payload = json.loads(json_path.read_text(encoding="utf-8"))
            self.assertEqual(payload["statistics"]["sections_count"], 4)

    def test_cli_writes_markdown_and_json_reports(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            tmp_path = Path(tmp_dir)
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

            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn("\033[", result.stdout)
            self.assertIn("Memory Health Analytics", result.stdout)
            self.assertTrue(output_path.exists())

            logs_dir = tmp_path / "logs"
            matching_reports = sorted(logs_dir.glob("memory_analytics_*.json"))
            self.assertTrue(matching_reports, "expected a JSON analytics report")
            payload = json.loads(matching_reports[0].read_text(encoding="utf-8"))
            self.assertEqual(payload["missing_cross_references"][0]["anchor"], "missing-anchor")


if __name__ == "__main__":
    unittest.main()
