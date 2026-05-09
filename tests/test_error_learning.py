import json
import tempfile
import unittest
from pathlib import Path

from src.self_improvement.error_learning import ErrorRecord, LearningsDB


class ErrorLearningTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp_dir.cleanup)
        self.db_path = Path(self.temp_dir.name) / "learnings.json"

    def test_log_error_persists_round_trip(self):
        db = LearningsDB(self.db_path)
        r = db.log_error("syntax", "bad indent", "use 4 spaces per level")
        self.assertEqual(r.category, "syntax")
        self.assertTrue(r.timestamp)
        self.assertTrue(self.db_path.exists())

        db2 = LearningsDB(self.db_path)
        loaded = db2.get_recent_learnings(limit=100)
        self.assertEqual(len(loaded), 1)
        self.assertEqual(loaded[0].corrective_action, "use 4 spaces per level")

    def test_get_recent_learnings_order_and_limit(self):
        db = LearningsDB(self.db_path)
        db.log_error("a", "one", "fix1", timestamp="2026-01-01T00:00:00+00:00")
        db.log_error("b", "two", "fix2", timestamp="2026-01-02T00:00:00+00:00")
        db.log_error("c", "three", "fix3", timestamp="2026-01-03T00:00:00+00:00")
        recent = db.get_recent_learnings(limit=2)
        self.assertEqual([x.category for x in recent], ["c", "b"])
        self.assertEqual(db.get_recent_learnings(limit=0), [])

    def test_export_learnings_markdown(self):
        db = LearningsDB(self.db_path)
        md_empty = db.export_learnings_markdown()
        self.assertIn("No learnings", md_empty)

        db.log_error("test", "desc line", "do X", timestamp="2026-05-01T12:00:00+00:00")
        md = db.export_learnings_markdown(limit=5)
        self.assertIn("# Error learnings", md)
        self.assertIn("## 1. test", md)
        self.assertIn("**Description:** desc line", md)
        self.assertIn("**Corrective action:** do X", md)

    def test_load_legacy_list_format(self):
        self.db_path.write_text(
            json.dumps(
                [
                    {
                        "timestamp": "t0",
                        "category": "c",
                        "description": "d",
                        "corrective_action": "a",
                    }
                ]
            ),
            encoding="utf-8",
        )
        db = LearningsDB(self.db_path)
        loaded = db.get_recent_learnings(limit=10)
        self.assertEqual(len(loaded), 1)
        self.assertEqual(loaded[0].category, "c")

    def test_error_record_immutable(self):
        r = ErrorRecord(
            timestamp="t",
            category="c",
            description="d",
            corrective_action="x",
        )
        d = r.to_dict()
        self.assertEqual(ErrorRecord.from_dict(d), r)
