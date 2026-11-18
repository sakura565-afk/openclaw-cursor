import importlib.util
import json
import tempfile
import unittest
from pathlib import Path


MODULE_PATH = Path(__file__).resolve().parent.parent / "scripts" / "optimize_context.py"
SPEC = importlib.util.spec_from_file_location("optimize_context", MODULE_PATH)
optimize_context = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
SPEC.loader.exec_module(optimize_context)


class OptimizeContextTests(unittest.TestCase):
    def test_analyze_session_reports_stale_large_and_memory_bloat(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            src = root / "src"
            memory = root / "memory"
            logs = root / "logs"
            src.mkdir()
            memory.mkdir()
            logs.mkdir()

            active_file = src / "active.py"
            stale_file = src / "stale.py"
            large_file = src / "large.md"
            active_file.write_text("def current_task():\n    return 'active'\n", encoding="utf-8")
            stale_file.write_text("class OldTask:\n    pass\n", encoding="utf-8")
            large_file.write_text("# Notes\n" + ("detail " * 2500), encoding="utf-8")

            duplicate_text = "shared memory block " * 120
            (memory / "dup_a.md").write_text(duplicate_text, encoding="utf-8")
            (memory / "dup_b.md").write_text(duplicate_text, encoding="utf-8")
            (memory / "large_memory.md").write_text("context " * 2200, encoding="utf-8")

            session_log = root / "session.json"
            session_log.write_text(
                json.dumps(
                    [
                        {"turn": 1, "files": ["src/stale.py"]},
                        {"turn": 2, "files": ["src/large.md"]},
                        {"turn": 8, "files": ["src/active.py"]},
                    ]
                ),
                encoding="utf-8",
            )

            report = optimize_context.analyze_session(
                root,
                session_log_path=session_log,
                stale_turns=3,
            )

            self.assertEqual(report["summary"]["loaded_file_count"], 3)
            self.assertGreater(report["summary"]["total_estimated_tokens"], 0)
            self.assertLess(report["summary"]["efficiency_score"], 100)
            self.assertTrue(
                any(item["path"] == "src/stale.py" for item in report["stale_candidates"]),
                report["stale_candidates"],
            )
            self.assertTrue(
                any(item["path"] == "src/large.md" for item in report["largest_in_context"]),
                report["largest_in_context"],
            )
            self.assertEqual(len(report["memory_bloat"]["duplicate_groups"]), 1)
            self.assertTrue(report["optimization_hints"])
            self.assertTrue(
                any(item["path"] == "src/stale.py" for item in report["load_priorities"]),
                report["load_priorities"],
            )
            self.assertTrue(
                any(item["path"] == "src/large.md" for item in report["reminders"]),
                report["reminders"],
            )

    def test_text_session_log_extracts_paths_and_turns(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            (root / "docs").mkdir()
            target = root / "docs" / "guide.md"
            target.write_text("# Guide\n\nUseful context\n", encoding="utf-8")

            session_log = root / "session.log"
            session_log.write_text(
                "turn 1 loaded docs/guide.md\n"
                "turn 4 revisited docs/guide.md for planning\n",
                encoding="utf-8",
            )

            refs, current_turn = optimize_context.parse_session_log(session_log, root)

            self.assertEqual(current_turn, 4)
            self.assertEqual(len(refs), 1)
            entry = next(iter(refs.values()))
            self.assertEqual(entry["display_path"], "docs/guide.md")
            self.assertEqual(entry["last_access_turn"], 4)
            self.assertEqual(entry["mentions"], 2)

    def test_write_report_generates_json_and_markdown(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            report = {
                "summary": {
                    "efficiency_score": 82,
                    "loaded_file_count": 2,
                    "total_estimated_tokens": 123,
                    "stale_candidate_count": 1,
                },
                "stale_candidates": [
                    {
                        "path": "src/old.py",
                        "priority": "high",
                        "token_estimate": 42,
                        "turns_since_access": 6,
                    }
                ],
                "load_priorities": [
                    {
                        "path": "src/new.py",
                        "priority": "high",
                        "token_estimate": 20,
                        "turns_since_access": 0,
                    }
                ],
                "reminders": [{"path": "src/new.py", "summary": "src/new.py: keep only the public API."}],
                "optimization_hints": ["Unload src/old.py first."],
                "largest_in_context": [],
                "memory_bloat": {
                    "files": [],
                    "large_files": [],
                    "duplicate_groups": [],
                    "near_duplicates": [],
                    "bloat_token_penalty": 0,
                },
            }
            output = root / "artifacts" / "hints.md"

            json_path, output_path = optimize_context.write_report(report, root, output)

            self.assertTrue(json_path.exists())
            self.assertTrue(output_path.exists())
            self.assertIn("Context Optimization Hints", output_path.read_text(encoding="utf-8"))
            self.assertEqual(json.loads(json_path.read_text(encoding="utf-8"))["summary"]["efficiency_score"], 82)

    def test_render_summary_includes_ansi_sequences_when_enabled(self):
        report = {
            "summary": {
                "loaded_file_count": 1,
                "total_estimated_tokens": 10,
                "efficiency_score": 50,
            },
            "stale_candidates": [],
            "largest_in_context": [],
            "memory_bloat": {
                "files": [],
                "large_files": [],
                "duplicate_groups": [],
                "near_duplicates": [],
                "bloat_token_penalty": 0,
            },
            "reminders": [],
            "optimization_hints": ["Keep src/core.py loaded first."],
        }

        summary = optimize_context.render_summary(report, use_color=True)

        self.assertIn("\033[", summary)
        self.assertIn("Optimization hints", summary)


if __name__ == "__main__":
    unittest.main()
