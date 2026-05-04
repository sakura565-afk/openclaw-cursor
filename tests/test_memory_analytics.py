import importlib.util
import io
import json
import tempfile
import unittest
from contextlib import redirect_stdout
from datetime import date
from pathlib import Path


MODULE_PATH = Path(__file__).resolve().parents[1] / "scripts" / "memory_analytics.py"
SPEC = importlib.util.spec_from_file_location("memory_analytics", MODULE_PATH)
memory_analytics = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
SPEC.loader.exec_module(memory_analytics)


SAMPLE_MEMORY = """# Daily Notes

- 2026-03-01 Reviewed architecture and linked [alpha](#project-alpha).
- 2026-01-01 Old integration note that has not been touched recently.

## Project Alpha

- 2026-02-15 Added rollout checklist and linked [ghost](#missing-anchor).
- 2026-02-20 Investigated cache issue with unusual memory usage pattern.

## Follow Up

- 2026-02-21 Investigated cache issue with unusual memory usage patterns.
<a id="custom-anchor"></a>
See [custom](#custom-anchor) and [alpha-again](#project-alpha).
"""


class MemoryAnalyticsTest(unittest.TestCase):
    def write_memory(self, directory: Path, content: str = SAMPLE_MEMORY) -> Path:
        memory_path = directory / "MEMORY.md"
        memory_path.write_text(content, encoding="utf-8")
        return memory_path

    def test_parse_memory_file_extracts_sections_entries_dates_and_links(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            memory_path = self.write_memory(Path(tmp_dir))

            parsed = memory_analytics.parse_memory_file(memory_path)

            self.assertEqual(len(parsed["sections"]), 3)
            self.assertEqual([section.title for section in parsed["sections"]], ["Daily Notes", "Project Alpha", "Follow Up"])
            self.assertEqual(len(parsed["entries"]), 6)
            self.assertEqual(parsed["entries"][0].dates[0].isoformat(), "2026-03-01")
            self.assertIn("project-alpha", parsed["anchors"])
            self.assertIn("custom-anchor", parsed["anchors"])
            self.assertEqual(len(parsed["internal_links"]), 4)

    def test_analysis_finds_stale_missing_refs_and_duplicates(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            memory_path = self.write_memory(Path(tmp_dir))
            parsed = memory_analytics.parse_memory_file(memory_path)

            report = memory_analytics.analyze_memory(
                parsed,
                stale_days=30,
                reference_date=date(2026, 3, 10),
            )

            self.assertEqual(report["summary"]["total_entries"], 6)
            self.assertEqual(report["summary"]["sections_count"], 3)
            self.assertEqual(report["age_distribution"]["8-30 days"], 3)
            self.assertEqual(report["age_distribution"]["31-90 days"], 2)
            self.assertEqual(report["age_distribution"]["0-7 days"], 1)
            self.assertEqual(len(report["stale_entries"]), 1)
            self.assertEqual(report["stale_entries"][0]["last_mention"], "2026-01-01")
            self.assertEqual(len(report["missing_cross_references"]), 1)
            self.assertEqual(report["missing_cross_references"][0]["target"], "missing-anchor")
            self.assertEqual(len(report["duplicate_entries"]), 1)
            self.assertGreaterEqual(report["duplicate_entries"][0]["similarity"], 0.8)

    def test_render_markdown_report_contains_health_sections(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            memory_path = self.write_memory(Path(tmp_dir))
            parsed = memory_analytics.parse_memory_file(memory_path)
            report = memory_analytics.analyze_memory(
                parsed,
                stale_days=30,
                reference_date=date(2026, 3, 10),
            )

            markdown = memory_analytics.render_markdown_report(report)

            self.assertIn("# Memory Health Report", markdown)
            self.assertIn("## Summary", markdown)
            self.assertIn("## Stale Entries (1)", markdown)
            self.assertIn("## Missing Cross References (1)", markdown)
            self.assertIn("## Duplicate Entries (1)", markdown)
            self.assertIn("`Project Alpha`", markdown)

    def test_main_writes_markdown_and_json_reports(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            temp_path = Path(tmp_dir)
            memory_path = self.write_memory(temp_path)
            output_path = temp_path / "health-report.md"
            expected_json = temp_path / "logs" / f"memory_analytics_{date.today().strftime('%Y%m%d')}.json"

            current_dir = Path.cwd()
            try:
                os_chdir = __import__("os").chdir
                os_chdir(temp_path)
                stdout = io.StringIO()
                with redirect_stdout(stdout):
                    exit_code = memory_analytics.main(
                        ["--input", str(memory_path), "--output", str(output_path), "--days", "45"]
                    )
            finally:
                os_chdir(current_dir)

            self.assertEqual(exit_code, 0)
            self.assertTrue(output_path.exists())
            self.assertTrue(expected_json.exists())
            self.assertIn("\033[", stdout.getvalue())

            json_report = json.loads(expected_json.read_text(encoding="utf-8"))
            self.assertEqual(json_report["input_file"], str(memory_path))
            self.assertEqual(json_report["stale_days"], 45)
            self.assertIn("summary", json_report)


if __name__ == "__main__":
    unittest.main()
