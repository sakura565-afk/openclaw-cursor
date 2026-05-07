import importlib.util
import json
import sqlite3
import sys
import tempfile
import unittest
from contextlib import redirect_stderr
from datetime import date
from io import StringIO
from pathlib import Path


REPO = Path(__file__).resolve().parents[1]


def _load_script(name: str, relative: str):
    path = REPO / relative
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    assert spec.loader is not None
    spec.loader.exec_module(mod)
    return mod


memory_health_report = _load_script("memory_health_report_mod", "scripts/memory_health_report.py")
memory_cleanup = _load_script("memory_cleanup_mod", "scripts/memory_cleanup.py")


class MemoryHealthReportTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tempdir = tempfile.TemporaryDirectory()
        self.workspace = Path(self.tempdir.name)

    def tearDown(self) -> None:
        self.tempdir.cleanup()

    def write(self, rel: str, text: str) -> Path:
        p = self.workspace / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text, encoding="utf-8")
        return p

    def test_health_report_surfaces_bloat_orphans_stale_and_embedding_quality(self) -> None:
        giant = "x" * 15000
        self.write(
            "MEMORY.md",
            "# Log\n\n"
            "- 2026-03-01 Recent [broken](#nope) note.\n\n"
            "## Old block\n"
            "- 2020-01-01 Something ancient we should flag as stale.\n",
        )
        self.write(
            "memory/2026-05-01.md",
            f"## Oversized daily\nUpdated: 2026-05-01\n\n{giant}\n",
        )

        self.write(
            "embeddings/sample.json",
            json.dumps({"chunks": [{"id": "a", "embedding": [0.0, 0.0, 0.0]}]}),
        )

        db_path = self.workspace / "vector_store.sqlite3"
        conn = sqlite3.connect(db_path)
        conn.execute("CREATE TABLE chunks (source_path TEXT)")
        conn.execute("INSERT INTO chunks VALUES (?)", ("/this/path/does/not/exist_12345.md",))
        conn.commit()
        conn.close()

        report = memory_health_report.build_report(
            self.workspace,
            stale_days=365,
            max_chunk_chars=8000,
            reference_date=date(2026, 5, 4),
        )

        self.assertEqual(report["workspace"], self.workspace.resolve().as_posix())
        categories = {issue["category"] for issue in report["issues"]}
        self.assertIn("bloated_chunk", categories)
        self.assertIn("orphaned_entry", categories)
        self.assertIn("low_quality_embedding", categories)
        self.assertIn("outdated_fact", categories)
        self.assertTrue(any(h.get("action") == "run_memory_cleanup" for h in report["cleanup_hints"]))
        self.assertIn("recommendations", report)
        self.assertGreaterEqual(report["summary"]["health_score"], 0)

    def test_merge_cleanup_hints_from_report_json(self) -> None:
        merged_days, merged_backup = memory_cleanup.merge_cleanup_hints(
            {
                "cleanup_hints": [
                    {"action": "run_memory_cleanup", "kwargs": {"days": 42, "backup": True}},
                    {"action": "manual_review", "kwargs": {}},
                ]
            },
            days=90,
            backup=False,
        )
        self.assertEqual(merged_days, 42)
        self.assertTrue(merged_backup)

    def test_cleanup_cli_with_health_report_and_verbose_logs(self) -> None:
        self.write(
            "MEMORY.md",
            "## Keep\nUpdated: 2026-05-01\n\nStable fact for the week.\n",
        )
        report = memory_health_report.build_report(
            self.workspace,
            stale_days=30,
            reference_date=date(2026, 5, 4),
        )
        report_path = self.workspace / "health.json"
        report_path.write_text(json.dumps(report), encoding="utf-8")

        stderr = StringIO()
        with redirect_stderr(stderr):
            memory_cleanup.configure_logging(verbose=True, quiet=False)
            code = memory_cleanup.main(
                argv=[
                    "--dry-run",
                    "--health-report",
                    str(report_path),
                    "--apply-report",
                    "-v",
                ],
                root=self.workspace,
                today=date(2026, 5, 4),
            )

        self.assertEqual(code, 0)
        self.assertIn("Health summary", stderr.getvalue())


if __name__ == "__main__":
    unittest.main()
